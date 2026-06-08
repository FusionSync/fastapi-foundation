from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.base import create_router
from core.context import get_current_context
from core.db import unit_of_work
from core.events import EventRegistry
from core.exceptions import AppError
from core.outbox import OutboxEventPublisher, OutboxRepository
from core.permissions import AuthorizationDecision, route_authorization_decision_for
from core.serialization import Envelope, ListEnvelope, Pagination, ok, ok_list
from platform_apps.access.schemas import (
    PermissionRead,
    PlatformAdminGrantRequest,
    RoleGrantRead,
)
from platform_apps.access.services import AccessCatalogService, PlatformAdminService

permission_router = create_router(
    "/platform/access/permissions",
    tags=["platform-access"],
    tenant_required=False,
    permissions=["access.permission:read"],
    permission_scope="platform",
)
platform_admin_router = create_router(
    "/platform/access/platform-admins",
    tags=["platform-access"],
    tenant_required=False,
    permissions=["access.platform_admin:manage"],
    permission_scope="platform",
    tenant_operation="write",
)

router = permission_router


@permission_router.get("", response_model=ListEnvelope[PermissionRead])
async def list_permissions(request: Request) -> dict[str, object]:
    registry = getattr(request.app.state, "permission_registry", None)
    if registry is None:
        raise AppError("SYSTEM_ERROR", "Permission registry is not configured", status_code=500)
    items = AccessCatalogService(registry).list_permissions()
    return ok_list(
        items,
        Pagination(
            total=len(items),
            page=1,
            page_size=max(len(items), 1),
            has_next=False,
        ),
    )


@platform_admin_router.post("", response_model=Envelope[RoleGrantRead])
async def grant_platform_admin(
    request: Request,
    payload: PlatformAdminGrantRequest,
    decision: Annotated[
        AuthorizationDecision,
        Depends(
            route_authorization_decision_for(
                "access.platform_admin:manage",
                scope="platform",
            )
        ),
    ],
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        grant = await PlatformAdminService(
            session,
            _event_publisher(request, session),
        ).grant_platform_admin(
            user_id=payload.user_id,
            role_template_id=payload.role_template_id,
            actor_id=context.user_id,
            request_id=context.request_id,
            authorization_decision=decision,
            reason=payload.reason,
        )
        return ok(_role_grant_read(grant))


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


def _role_grant_read(grant) -> dict[str, object]:
    return {
        "id": grant.id,
        "tenant_id": grant.tenant_id,
        "subject_type": grant.subject_type,
        "subject_id": grant.subject_id,
        "role_template_id": grant.role_template_id,
        "policy_version": grant.policy_version,
    }
