from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.base import create_router
from core.context import get_current_context
from core.db import unit_of_work
from core.events import EventRegistry
from core.exceptions import AppError
from core.outbox import OutboxEventPublisher, OutboxRepository
from core.permissions import AuthorizationDecision, route_authorization_decision
from core.serialization import Envelope, ListEnvelope, ok, ok_list
from platform_apps.tenants.schemas import (
    TenantCreateRequest,
    TenantInvitationAcceptRequest,
    TenantInvitationIssuedRead,
    TenantInvitationIssueRequest,
    TenantInvitationRead,
    TenantListQuery,
    TenantMemberCreateRequest,
    TenantMemberListQuery,
    TenantMemberRead,
    TenantMemberUpdateRequest,
    TenantRead,
)
from platform_apps.tenants.services import (
    TenantInvitationIssue,
    TenantInvitationService,
    TenantLifecycleService,
    TenantMembershipService,
    TenantQueryService,
)

platform_router = create_router(
    "/platform/tenants",
    tags=["platform-tenants"],
    tenant_required=False,
    permissions=["tenant:manage"],
    permission_scope="platform",
)
member_read_router = create_router(
    "/tenants/{tenant_id}/members",
    tags=["tenant-members"],
    permissions=["tenant_member:read"],
)
member_manage_router = create_router(
    "/tenants/{tenant_id}/members",
    tags=["tenant-members"],
    permissions=["tenant_member:manage"],
    tenant_operation="write",
)
invitation_issue_router = create_router(
    "/tenants/{tenant_id}/invitations",
    tags=["tenant-invitations"],
    permissions=["tenant_invitation:invite"],
    tenant_operation="write",
)
invitation_revoke_router = create_router(
    "/tenants/{tenant_id}/invitations",
    tags=["tenant-invitations"],
    permissions=["tenant_invitation:revoke"],
    tenant_operation="write",
)
invitation_accept_router = create_router(
    "/tenant-invitations",
    tags=["tenant-invitations"],
    tenant_required=False,
)

# Backward-compatible name for callers that imported the original single router.
router = platform_router


@platform_router.get("", response_model=ListEnvelope[TenantRead])
async def list_tenants(
    request: Request,
    query: Annotated[TenantListQuery, Depends()],
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        tenants, total = await TenantQueryService(session).list_tenants(query)
        return ok_list(
            [_tenant_read(tenant) for tenant in tenants],
            query.to_pagination(total=total),
        )


@platform_router.post("", response_model=Envelope[TenantRead])
async def provision_tenant(
    request: Request,
    payload: TenantCreateRequest,
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        tenant = await TenantLifecycleService(
            session,
            _event_publisher(request, session),
        ).provision_tenant(
            tenant_id=payload.id,
            name=payload.name,
            code=payload.code,
            owner_user_id=payload.owner_user_id,
            deployment_mode=payload.deployment_mode,
            actor_id=context.user_id,
            request_id=context.request_id,
            authorization_decision=decision,
        )
        return ok(_tenant_read(tenant))


@member_read_router.get("", response_model=ListEnvelope[TenantMemberRead])
async def list_tenant_members(
    request: Request,
    tenant_id: str,
    query: Annotated[TenantMemberListQuery, Depends()],
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        members, total = await TenantMembershipService(
            session,
            _event_publisher(request, session),
        ).list_members(
            tenant_id=tenant_id,
            query=query,
            actor_id=context.user_id,
            authorization_decision=decision,
        )
        return ok_list(
            [_member_read(member) for member in members],
            query.to_pagination(total=total),
        )


@member_manage_router.post("", response_model=Envelope[TenantMemberRead])
async def create_tenant_member(
    request: Request,
    tenant_id: str,
    payload: TenantMemberCreateRequest,
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        member = await TenantMembershipService(
            session,
            _event_publisher(request, session),
        ).create_member(
            tenant_id=tenant_id,
            user_id=payload.user_id,
            status=payload.status,
            actor_id=context.user_id,
            request_id=context.request_id,
            authorization_decision=decision,
        )
        return ok(_member_read(member))


@member_manage_router.patch("/{member_id}", response_model=Envelope[TenantMemberRead])
async def update_tenant_member(
    request: Request,
    tenant_id: str,
    member_id: str,
    payload: TenantMemberUpdateRequest,
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        member = await TenantMembershipService(
            session,
            _event_publisher(request, session),
        ).update_member_status(
            tenant_id=tenant_id,
            member_id=member_id,
            status=payload.status,
            actor_id=context.user_id,
            request_id=context.request_id,
            authorization_decision=decision,
        )
        return ok(_member_read(member))


@invitation_issue_router.post("", response_model=Envelope[TenantInvitationIssuedRead])
async def issue_tenant_invitation(
    request: Request,
    tenant_id: str,
    payload: TenantInvitationIssueRequest,
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        issued = await TenantInvitationService(
            session,
            _event_publisher(request, session),
        ).issue_invitation(
            tenant_id=tenant_id,
            email=payload.email,
            role_template_id=payload.role_template_id,
            actor_id=context.user_id,
            request_id=context.request_id,
            expires_at=payload.expires_at,
            authorization_decision=decision,
        )
        return ok(_invitation_issued_read(issued))


@invitation_revoke_router.patch(
    "/{invitation_id}/revoke",
    response_model=Envelope[TenantInvitationRead],
)
async def revoke_tenant_invitation(
    request: Request,
    tenant_id: str,
    invitation_id: str,
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        service = TenantInvitationService(session, _event_publisher(request, session))
        invitation = await service.get_invitation(
            tenant_id=tenant_id,
            invitation_id=invitation_id,
        )
        revoked = await service.revoke_invitation(
            invitation,
            actor_id=context.user_id,
            request_id=context.request_id,
            authorization_decision=decision,
        )
        return ok(_invitation_read(revoked))


@invitation_accept_router.post("/accept", response_model=Envelope[TenantInvitationRead])
async def accept_tenant_invitation(
    request: Request,
    payload: TenantInvitationAcceptRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        invitation = await TenantInvitationService(
            session,
            _event_publisher(request, session),
        ).accept_invitation(
            token=payload.token,
            user_id=context.user_id,
            email=payload.email,
            actor_id=context.user_id,
            request_id=context.request_id,
        )
        return ok(_invitation_read(invitation))


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


def _tenant_read(tenant) -> dict[str, object]:
    return {
        "id": tenant.id,
        "name": tenant.name,
        "code": tenant.code,
        "status": tenant.status,
        "deployment_mode": tenant.deployment_mode,
    }


def _member_read(member) -> dict[str, object]:
    return {
        "id": member.id,
        "tenant_id": member.tenant_id,
        "user_id": member.user_id,
        "status": member.status,
    }


def _invitation_read(invitation) -> dict[str, object]:
    return {
        "id": invitation.id,
        "tenant_id": invitation.tenant_id,
        "email": invitation.email,
        "role_template_id": invitation.role_template_id,
        "status": invitation.status,
        "expires_at": _aware_datetime(invitation.expires_at),
        "invited_by_user_id": invitation.invited_by_user_id,
        "accepted_by_user_id": invitation.accepted_by_user_id,
    }


def _invitation_issued_read(issued: TenantInvitationIssue) -> dict[str, object]:
    return {**_invitation_read(issued.invitation), "token": issued.token}


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
