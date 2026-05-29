from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import LocalJwtConfig, LocalJwtProvider, TokenClaims, invalid_auth_token
from core.base import create_router
from core.context import get_current_context
from core.db import unit_of_work
from core.events import EventRegistry
from core.exceptions import AppError
from core.outbox import OutboxEventPublisher, OutboxRepository
from core.permissions import AuthorizationDecision, route_authorization_decision
from core.serialization import Envelope, ListEnvelope, ok, ok_list
from platform_apps.accounts.models import ExternalIdentity, User, UserSession
from platform_apps.accounts.schemas import (
    ExternalIdentityCreateRequest,
    ExternalIdentityRead,
    LoginRead,
    LoginRequest,
    PasswordResetRead,
    PasswordResetRequest,
    SessionRevokeRead,
    SessionRevokeRequest,
    TokenRefreshRead,
    UserCreateRequest,
    UserProfileUpdateRequest,
    UserRead,
    UserSessionDetailRead,
)
from platform_apps.accounts.services import AccountsService

auth_public_router = create_router("/auth", tags=["auth"], public=True)
auth_router = create_router("/auth", tags=["auth"], tenant_required=False)
me_router = create_router("/me", tags=["accounts"], tenant_required=False)
platform_user_router = create_router(
    "/platform/accounts/users",
    tags=["platform-accounts"],
    tenant_required=False,
    permissions=["user:manage"],
    permission_scope="platform",
)
platform_session_router = create_router(
    "/platform/accounts/users",
    tags=["platform-accounts"],
    tenant_required=False,
    permissions=["session:revoke"],
    permission_scope="platform",
)

# Backward-compatible name for callers that imported the original single router.
router = platform_user_router


@auth_public_router.post("/login", response_model=Envelope[LoginRead])
async def login(request: Request, payload: LoginRequest) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        user_session = await _accounts(request, session).authenticate_local_login(
            email=payload.email,
            password=payload.password,
            tenant_id=payload.tenant_id,
            request_id=_request_id(),
        )
        return ok(_login_read(request, user_session))


@auth_router.post("/refresh", response_model=Envelope[TokenRefreshRead])
async def refresh_token(request: Request) -> dict[str, object]:
    claims = _request_claims(request)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        refreshed = await _accounts(request, session).refresh_session_token(
            claims,
            request_id=_request_id(),
        )
        user_session = await session.get(UserSession, refreshed.session_id)
        if user_session is None:
            invalid_auth_token("session_not_found")
        return ok(_login_read(request, user_session))


@auth_router.post("/logout", response_model=Envelope[SessionRevokeRead])
async def logout(request: Request) -> dict[str, object]:
    claims = _request_claims(request)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        revoked = await _accounts(request, session).revoke_own_session(
            user_id=claims.user_id,
            session_id=claims.session_id,
            reason="logout",
        )
        return ok({"revoked_sessions": revoked})


@me_router.get("", response_model=Envelope[UserRead])
async def get_me(request: Request) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        user = await _active_session(uow.session).get(User, context.user_id)
        if user is None:
            invalid_auth_token("user_not_found")
        return ok(_user_read(user))


@me_router.patch("", response_model=Envelope[UserRead])
async def update_me(
    request: Request,
    payload: UserProfileUpdateRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        user = await _accounts(request, session).update_profile(
            context.user_id,
            display_name=payload.display_name,
        )
        return ok(_user_read(user))


@me_router.patch("/password", response_model=Envelope[PasswordResetRead])
async def reset_my_password(
    request: Request,
    payload: PasswordResetRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        await _accounts(request, _active_session(uow.session)).reset_local_password(
            context.user_id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
        return ok({"password_updated": True})


@me_router.post("/external-identities", response_model=Envelope[ExternalIdentityRead])
async def bind_my_external_identity(
    request: Request,
    payload: ExternalIdentityCreateRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        identity = await _accounts(
            request,
            _active_session(uow.session),
        ).bind_external_identity(
            context.user_id,
            provider=payload.provider,
            subject=payload.subject,
        )
        return ok(_identity_read(identity))


@me_router.get("/external-identities", response_model=ListEnvelope[ExternalIdentityRead])
async def list_my_external_identities(request: Request) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        identities = await _accounts(
            request,
            _active_session(uow.session),
        ).list_external_identities(context.user_id)
        pagination = {
            "total": len(identities),
            "page": 1,
            "page_size": len(identities) or 1,
            "has_next": False,
        }
        return ok_list(
            [_identity_read(identity) for identity in identities],
            pagination,
        )


@me_router.get("/sessions", response_model=ListEnvelope[UserSessionDetailRead])
async def list_my_sessions(request: Request) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        sessions = await _accounts(
            request,
            _active_session(uow.session),
        ).list_user_sessions(context.user_id)
        pagination = {
            "total": len(sessions),
            "page": 1,
            "page_size": len(sessions) or 1,
            "has_next": False,
        }
        return ok_list(
            [_session_detail_read(user_session) for user_session in sessions],
            pagination,
        )


@me_router.delete("/sessions/{session_id}", response_model=Envelope[SessionRevokeRead])
async def revoke_my_session(request: Request, session_id: str) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        revoked = await _accounts(request, _active_session(uow.session)).revoke_own_session(
            user_id=context.user_id,
            session_id=session_id,
            reason="user requested",
        )
        return ok({"revoked_sessions": revoked})


@platform_user_router.post("", response_model=Envelope[UserRead])
async def create_platform_user(
    request: Request,
    payload: UserCreateRequest,
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        user = await _accounts(request, _active_session(uow.session)).create_local_user(
            email=payload.email,
            display_name=payload.display_name,
            password=payload.password,
        )
        return ok(_user_read(user))


@platform_user_router.patch("/{user_id}/disable", response_model=Envelope[UserRead])
async def disable_platform_user(
    request: Request,
    user_id: str,
    payload: SessionRevokeRequest,
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        user = await _accounts(request, _active_session(uow.session)).disable_user(
            user_id,
            reason=payload.reason,
            actor_id=context.user_id,
            request_id=context.request_id,
            authorization_decision=decision,
        )
        return ok(_user_read(user))


@platform_session_router.post(
    "/{user_id}/sessions/revoke",
    response_model=Envelope[SessionRevokeRead],
)
async def revoke_platform_user_sessions(
    request: Request,
    user_id: str,
    payload: SessionRevokeRequest,
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        revoked = await _accounts(request, _active_session(uow.session)).revoke_user_sessions(
            user_id,
            payload.reason,
            actor_id=context.user_id,
            request_id=context.request_id,
            authorization_decision=decision,
        )
        return ok({"revoked_sessions": revoked})


def _accounts(request: Request, session: AsyncSession) -> AccountsService:
    return AccountsService(session, events=_event_publisher(request, session))


def _session_factory(request: Request):
    return request.app.state.session_factory


def _active_session(session: AsyncSession | None) -> AsyncSession:
    if session is None:
        raise AppError("SYSTEM_ERROR", "Database session is not available", status_code=500)
    return session


def _event_publisher(request: Request, session: AsyncSession) -> OutboxEventPublisher:
    registry = getattr(request.app.state, "event_registry", None)
    if registry is not None and not isinstance(registry, EventRegistry):
        raise AppError("SYSTEM_ERROR", "Event registry is invalid", status_code=500)
    return OutboxEventPublisher(OutboxRepository(session, registry=registry))


def _request_context():
    context = get_current_context()
    if context is None or not context.user_id:
        raise AppError("AUTH_INVALID_TOKEN", "Authenticated user is required", status_code=401)
    return context


def _request_id() -> str | None:
    context = get_current_context()
    return context.request_id if context is not None else None


def _request_claims(request: Request) -> TokenClaims:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        invalid_auth_token("invalid_authorization_header")
    return _jwt_provider(request).verify_token(token.strip())


def _jwt_provider(request: Request) -> LocalJwtProvider:
    return LocalJwtProvider(_jwt_config(request))


def _jwt_config(request: Request) -> LocalJwtConfig:
    return LocalJwtConfig(secret=request.app.state.settings.security.jwt_secret)


def _login_read(request: Request, user_session: UserSession) -> dict[str, object]:
    token = _jwt_provider(request).issue_token(
        TokenClaims(
            user_id=user_session.user_id,
            session_id=user_session.id,
            auth_provider=user_session.auth_provider,
            token_version=user_session.token_version,
            tenant_id=user_session.tenant_id,
        )
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": _jwt_config(request).expires_in_seconds,
        "session": _session_read(user_session),
    }


def _user_read(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "status": user.status,
        "auth_provider": user.auth_provider,
    }


def _session_read(user_session: UserSession) -> dict[str, object]:
    return {
        "id": user_session.id,
        "user_id": user_session.user_id,
        "tenant_id": user_session.tenant_id,
        "status": user_session.status,
        "auth_provider": user_session.auth_provider,
    }


def _session_detail_read(user_session: UserSession) -> dict[str, object]:
    return {
        **_session_read(user_session),
        "revoke_reason": user_session.revoke_reason,
        "revoked_at": user_session.revoked_at,
    }


def _identity_read(identity: ExternalIdentity) -> dict[str, object]:
    return {
        "id": identity.id,
        "user_id": identity.user_id,
        "provider": identity.provider,
        "subject": identity.subject,
    }
