from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from core.audit import AuditRecorder
from core.auth.errors import invalid_auth_token
from core.auth.jwt_provider import LocalJwtProvider
from core.auth.session import AuthSessionStore, AuthSessionValidator
from core.base import RouteSecurityPolicy, parse_route_permission
from core.context import RequestContext, get_current_context, set_current_context
from core.exceptions import AppError
from core.permissions import AuthorizationDecision, AuthorizationService
from core.tenancy import DatabaseTenantContextResolver

SessionStoreFactory = Callable[[AsyncSession], AuthSessionStore]
AuditFactory = Callable[[AsyncSession], AuditRecorder]


class DatabaseRequestSecurityPipeline:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        jwt_provider: LocalJwtProvider,
        session_store_factory: SessionStoreFactory,
        audit_factory: AuditFactory | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.jwt_provider = jwt_provider
        self.session_store_factory = session_store_factory
        self.audit_factory = audit_factory

    async def resolve(self, request: Request, policy: RouteSecurityPolicy) -> None:
        if not (policy.auth_required or policy.tenant_required or policy.permissions):
            return
        token = _bearer_token(request)
        claims = self.jwt_provider.verify_token(token)
        async with self.session_factory() as session:
            current_user = await AuthSessionValidator(
                self.session_store_factory(session)
            ).authenticate(claims)
            if _requires_tenant_resolution(policy):
                await DatabaseTenantContextResolver(session).resolve(
                    current_user=current_user,
                    token_tenant_id=claims.tenant_id,
                    header_tenant_id=request.headers.get("X-Tenant-ID"),
                    operation=policy.tenant_operation,  # type: ignore[arg-type]
                )
                return
            _bind_authenticated_user(current_user.id)

    async def authorize(
        self,
        context: RequestContext | None,
        policy: RouteSecurityPolicy,
    ) -> tuple[AuthorizationDecision, ...]:
        if not policy.permissions:
            return ()
        if context is None or not context.user_id:
            raise AppError(
                "AUTH_INVALID_TOKEN",
                "Authenticated user is required before permission authorization",
                status_code=401,
            )
        async with self.session_factory() as session:
            audit = self.audit_factory(session) if self.audit_factory is not None else None
            authorization = AuthorizationService(session, audit=audit)
            decisions: list[AuthorizationDecision] = []
            try:
                for permission in policy.permissions:
                    resource, action = parse_route_permission(permission)
                    if policy.permission_scope == "platform":
                        decisions.append(
                            await authorization.require_platform(
                                user_id=context.user_id,
                                resource=resource,
                                action=action,
                                request_id=context.request_id,
                            )
                        )
                        continue
                    if not context.tenant_id:
                        raise AppError(
                            "TENANT_ACCESS_DENIED",
                            "Tenant context is required before permission authorization",
                            status_code=403,
                        )
                    decisions.append(
                        await authorization.require(
                            user_id=context.user_id,
                            tenant_id=context.tenant_id,
                            resource=resource,
                            action=action,
                            request_id=context.request_id,
                        )
                    )
            except AppError as exc:
                if audit is not None and exc.code == "PERMISSION_DENIED":
                    await session.commit()
                raise
            return tuple(decisions)


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    if authorization is None:
        invalid_auth_token("missing_bearer_token")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        invalid_auth_token("invalid_authorization_header")
    return token.strip()


def _requires_tenant_resolution(policy: RouteSecurityPolicy) -> bool:
    return policy.tenant_required or (
        bool(policy.permissions) and policy.permission_scope != "platform"
    )


def _bind_authenticated_user(user_id: str) -> None:
    context = get_current_context()
    if context is None:
        return
    set_current_context(context.with_user(user_id).freeze())
