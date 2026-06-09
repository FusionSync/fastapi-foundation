from __future__ import annotations

import importlib
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.base import create_router
from core.context import get_current_context
from core.db import unit_of_work
from core.events import EventRegistry
from core.exceptions import AppError
from core.outbox import OutboxEventPublisher, OutboxRepository
from core.permissions import (
    AuthorizationDecision,
    PermissionRegistry,
    route_authorization_decision_for,
)
from core.serialization import Envelope, ListEnvelope, Pagination, ok, ok_list
from platform_apps.access.schemas import (
    EffectivePermissionRead,
    FrontendAccessCheckRead,
    FrontendAccessCheckRequest,
    FrontendAccessMappingCreateRequest,
    FrontendAccessMappingRead,
    FrontendAccessMappingRevisionRead,
    FrontendAccessMappingUpdateRequest,
    FrontendAccessRead,
    FrontendAccessValidateRead,
    FrontendAccessValidateRequest,
    PermissionCheckRead,
    PermissionCheckRequest,
    PermissionRead,
    PlatformAdminGrantRequest,
    ProjectionReconcileRead,
    ProjectionReconcileRequest,
    RoleGrantRead,
    RoleTemplateCreateRequest,
    RoleTemplateRead,
    RoleTemplateUpdateRequest,
    TenantRoleGrantCreateRequest,
)
from platform_apps.access.services import (
    AccessCatalogService,
    AccessProjectionService,
    EffectiveAccessService,
    FrontendAccessConfigService,
    FrontendAccessEvaluationService,
    PlatformAdminService,
    RoleTemplateService,
    TenantRoleGrantService,
)

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
role_template_read_router = create_router(
    "/platform/access/role-templates",
    tags=["platform-access"],
    tenant_required=False,
    permissions=["access.role_template:read"],
    permission_scope="platform",
)
role_template_manage_router = create_router(
    "/platform/access/role-templates",
    tags=["platform-access"],
    tenant_required=False,
    permissions=["access.role_template:manage"],
    permission_scope="platform",
    tenant_operation="write",
)
role_grant_read_router = create_router(
    "/access/role-grants",
    tags=["tenant-access"],
    permissions=["role_grant:read"],
)
role_grant_grant_router = create_router(
    "/access/role-grants",
    tags=["tenant-access"],
    permissions=["role_grant:grant"],
    tenant_operation="write",
)
role_grant_revoke_router = create_router(
    "/access/role-grants",
    tags=["tenant-access"],
    permissions=["role_grant:revoke"],
    tenant_operation="write",
)
me_permission_router = create_router(
    "/me/permissions",
    tags=["me"],
)
effective_access_router = create_router(
    "/platform/access/subjects",
    tags=["platform-access"],
    tenant_required=False,
    permissions=["access.effective:read"],
    permission_scope="platform",
)
projection_reconcile_router = create_router(
    "/platform/access/projections",
    tags=["platform-access"],
    tenant_required=False,
    permissions=["access.reconcile:manage"],
    permission_scope="platform",
    tenant_operation="write",
)
frontend_access_read_router = create_router(
    "/platform/access/frontend-access",
    tags=["platform-access"],
    tenant_required=False,
    permissions=["access.frontend_config:read"],
    permission_scope="platform",
)
frontend_access_manage_router = create_router(
    "/platform/access/frontend-access",
    tags=["platform-access"],
    tenant_required=False,
    permissions=["access.frontend_config:manage"],
    permission_scope="platform",
    tenant_operation="write",
)
me_access_router = create_router(
    "/me/access",
    tags=["me"],
    tenant_required=False,
)

router = permission_router


@permission_router.get("", response_model=ListEnvelope[PermissionRead])
async def list_permissions(request: Request) -> dict[str, object]:
    items = AccessCatalogService(_permission_registry(request)).list_permissions()
    return ok_list(
        items,
        Pagination(
            total=len(items),
            page=1,
            page_size=max(len(items), 1),
            has_next=False,
        ),
    )


@role_template_read_router.get("", response_model=ListEnvelope[RoleTemplateRead])
async def list_role_templates(
    request: Request,
    scope: str | None = None,
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        templates = await RoleTemplateService(
            session,
            _permission_registry(request),
        ).list_templates(scope=scope)
        return ok_list(
            [_role_template_read(template) for template in templates],
            Pagination(
                total=len(templates),
                page=1,
                page_size=max(len(templates), 1),
                has_next=False,
            ),
        )


@role_template_manage_router.post("", response_model=Envelope[RoleTemplateRead])
async def create_role_template(
    request: Request,
    payload: RoleTemplateCreateRequest,
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        template = await RoleTemplateService(
            session,
            _permission_registry(request),
        ).create_template(
            scope=payload.scope,
            name=payload.name,
            version=payload.version,
            permissions=[permission.model_dump() for permission in payload.permissions],
        )
        return ok(_role_template_read(template))


@role_template_manage_router.patch("/{template_id}", response_model=Envelope[RoleTemplateRead])
async def update_role_template(
    request: Request,
    template_id: str,
    payload: RoleTemplateUpdateRequest,
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        template = await RoleTemplateService(
            session,
            _permission_registry(request),
        ).update_template(
            template_id,
            name=payload.name,
            permissions=(
                [permission.model_dump() for permission in payload.permissions]
                if payload.permissions is not None
                else None
            ),
        )
        return ok(_role_template_read(template))


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
            audit=_audit_recorder(request, session),
        ).grant_platform_admin(
            user_id=payload.user_id,
            role_template_id=payload.role_template_id,
            actor_id=context.user_id,
            request_id=context.request_id,
            authorization_decision=decision,
            reason=payload.reason,
        )
        return ok(_role_grant_read(grant))


@role_grant_read_router.get("", response_model=ListEnvelope[RoleGrantRead])
async def list_tenant_role_grants(
    request: Request,
) -> dict[str, object]:
    context = _request_context()
    tenant_id = _current_tenant_id(context)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        grants = await TenantRoleGrantService(
            session,
            _event_publisher(request, session),
            permission_registry=_permission_registry(request),
        ).list_grants(tenant_id=tenant_id)
        return ok_list(
            [_role_grant_read(grant) for grant in grants],
            Pagination(
                total=len(grants),
                page=1,
                page_size=max(len(grants), 1),
                has_next=False,
            ),
        )


@role_grant_grant_router.post("", response_model=Envelope[RoleGrantRead])
async def grant_tenant_role(
    request: Request,
    payload: TenantRoleGrantCreateRequest,
    decision: Annotated[
        AuthorizationDecision,
        Depends(route_authorization_decision_for("role_grant:grant")),
    ],
) -> dict[str, object]:
    context = _request_context()
    tenant_id = _current_tenant_id(context)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        grant = await TenantRoleGrantService(
            session,
            _event_publisher(request, session),
            permission_registry=_permission_registry(request),
            audit=_audit_recorder(request, session),
        ).grant_role(
            tenant_id=tenant_id,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            role_template_id=payload.role_template_id,
            actor_id=context.user_id,
            request_id=context.request_id,
            authorization_decision=decision,
            reason=payload.reason,
        )
        return ok(_role_grant_read(grant))


@role_grant_revoke_router.delete("/{grant_id}", response_model=Envelope[RoleGrantRead])
async def revoke_tenant_role(
    request: Request,
    grant_id: str,
    decision: Annotated[
        AuthorizationDecision,
        Depends(route_authorization_decision_for("role_grant:revoke")),
    ],
    reason: str | None = None,
) -> dict[str, object]:
    context = _request_context()
    tenant_id = _current_tenant_id(context)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        grant = await TenantRoleGrantService(
            session,
            _event_publisher(request, session),
            permission_registry=_permission_registry(request),
            audit=_audit_recorder(request, session),
        ).revoke_role(
            tenant_id=tenant_id,
            grant_id=grant_id,
            actor_id=context.user_id,
            request_id=context.request_id,
            authorization_decision=decision,
            reason=reason,
        )
        return ok(_role_grant_read(grant))


@effective_access_router.get(
    "/{subject_type}/{subject_id}/effective-permissions",
    response_model=ListEnvelope[EffectivePermissionRead],
)
async def list_effective_permissions(
    request: Request,
    subject_type: str,
    subject_id: str,
    tenant_id: str,
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        policies = await EffectiveAccessService(session).list_effective_permissions(
            tenant_id=tenant_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        return ok_list(
            [_projected_policy_read(policy) for policy in policies],
            Pagination(
                total=len(policies),
                page=1,
                page_size=max(len(policies), 1),
                has_next=False,
            ),
        )


@me_permission_router.get("", response_model=ListEnvelope[EffectivePermissionRead])
async def list_my_permissions(request: Request) -> dict[str, object]:
    context = _request_context()
    tenant_id = _current_tenant_id(context)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        policies = await EffectiveAccessService(session).list_effective_permissions(
            tenant_id=tenant_id,
            subject_type="user",
            subject_id=context.user_id,
        )
        return ok_list(
            [_projected_policy_read(policy) for policy in policies],
            Pagination(
                total=len(policies),
                page=1,
                page_size=max(len(policies), 1),
                has_next=False,
            ),
        )


@me_permission_router.post("/check", response_model=Envelope[PermissionCheckRead])
async def check_my_permissions(
    request: Request,
    payload: PermissionCheckRequest,
) -> dict[str, object]:
    context = _request_context()
    tenant_id = _current_tenant_id(context)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        policies = await EffectiveAccessService(session).list_effective_permissions(
            tenant_id=tenant_id,
            subject_type="user",
            subject_id=context.user_id,
        )
        allowed = {(policy.resource, policy.action) for policy in policies}
        return ok(
            {
                "permissions": [
                    _permission_check_read(permission, allowed)
                    for permission in payload.permissions
                ]
            }
        )


@projection_reconcile_router.post(
    "/reconcile",
    response_model=Envelope[ProjectionReconcileRead],
)
async def reconcile_access_projections(
    request: Request,
    payload: ProjectionReconcileRequest,
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        result = await AccessProjectionService(
            session,
            permission_registry=_permission_registry(request),
        ).reconcile(repair=payload.repair)
        return ok(result.to_dict())


@frontend_access_read_router.get("", response_model=ListEnvelope[FrontendAccessMappingRead])
async def list_frontend_access_mappings(
    request: Request,
    client_id: str = "console-web",
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        mappings = await FrontendAccessConfigService(
            session,
            _permission_registry(request),
        ).list_mappings(client_id=client_id)
        return ok_list(
            [_frontend_access_mapping_read(mapping) for mapping in mappings],
            Pagination(
                total=len(mappings),
                page=1,
                page_size=max(len(mappings), 1),
                has_next=False,
            ),
        )


@frontend_access_manage_router.post("", response_model=Envelope[FrontendAccessMappingRead])
async def create_frontend_access_mapping(
    request: Request,
    payload: FrontendAccessMappingCreateRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        mapping = await FrontendAccessConfigService(
            session,
            _permission_registry(request),
            audit=_audit_recorder(request, session),
        ).create_mapping(
            client_id=payload.client_id,
            access_key=payload.access_key,
            owner_module=payload.owner_module,
            evaluation_scope=payload.evaluation_scope,
            expression=payload.expression,
            description=payload.description,
            actor_id=context.user_id,
            request_id=context.request_id,
            reason=payload.reason,
        )
        return ok(_frontend_access_mapping_read(mapping))


@frontend_access_manage_router.post(
    "/validate",
    response_model=Envelope[FrontendAccessValidateRead],
)
async def validate_frontend_access_mapping(
    request: Request,
    payload: FrontendAccessValidateRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        result = await FrontendAccessConfigService(
            session,
            _permission_registry(request),
            audit=_audit_recorder(request, session),
        ).validate(
            evaluation_scope=payload.evaluation_scope,
            expression=payload.expression,
            actor_id=context.user_id,
            request_id=context.request_id,
            reason=payload.reason,
        )
        return ok(result)


@frontend_access_read_router.get(
    "/{access_key}",
    response_model=Envelope[FrontendAccessMappingRead],
)
async def get_frontend_access_mapping(
    request: Request,
    access_key: str,
    client_id: str = "console-web",
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        mapping = await FrontendAccessConfigService(
            session,
            _permission_registry(request),
        ).get_mapping(client_id=client_id, access_key=access_key)
        return ok(_frontend_access_mapping_read(mapping))


@frontend_access_manage_router.patch(
    "/{access_key}",
    response_model=Envelope[FrontendAccessMappingRead],
)
async def update_frontend_access_mapping(
    request: Request,
    access_key: str,
    payload: FrontendAccessMappingUpdateRequest,
    client_id: str = "console-web",
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        mapping = await FrontendAccessConfigService(
            session,
            _permission_registry(request),
            audit=_audit_recorder(request, session),
        ).update_mapping(
            client_id=client_id,
            access_key=access_key,
            owner_module=payload.owner_module,
            evaluation_scope=payload.evaluation_scope,
            expression=payload.expression,
            description=payload.description,
            status=payload.status,
            actor_id=context.user_id,
            request_id=context.request_id,
            reason=payload.reason,
        )
        return ok(_frontend_access_mapping_read(mapping))


@frontend_access_manage_router.delete(
    "/{access_key}",
    response_model=Envelope[FrontendAccessMappingRead],
)
async def disable_frontend_access_mapping(
    request: Request,
    access_key: str,
    reason: str,
    client_id: str = "console-web",
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        mapping = await FrontendAccessConfigService(
            session,
            _permission_registry(request),
            audit=_audit_recorder(request, session),
        ).disable_mapping(
            client_id=client_id,
            access_key=access_key,
            actor_id=context.user_id,
            request_id=context.request_id,
            reason=reason,
        )
        return ok(_frontend_access_mapping_read(mapping))


@frontend_access_read_router.get(
    "/{access_key}/history",
    response_model=ListEnvelope[FrontendAccessMappingRevisionRead],
)
async def list_frontend_access_mapping_history(
    request: Request,
    access_key: str,
    client_id: str = "console-web",
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        revisions = await FrontendAccessConfigService(
            session,
            _permission_registry(request),
        ).list_revisions(client_id=client_id, access_key=access_key)
        return ok_list(
            [_frontend_access_mapping_revision_read(revision) for revision in revisions],
            Pagination(
                total=len(revisions),
                page=1,
                page_size=max(len(revisions), 1),
                has_next=False,
            ),
        )


@me_access_router.get("", response_model=Envelope[FrontendAccessRead])
async def list_my_frontend_access(
    request: Request,
    client_id: str = "console-web",
) -> dict[str, object]:
    context = _request_context()
    tenant_id = getattr(context, "tenant_id", None)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        result = await FrontendAccessEvaluationService(session).evaluate_all(
            client_id=client_id,
            tenant_id=tenant_id,
            user_id=context.user_id,
        )
        return ok(result)


@me_access_router.post("/check", response_model=Envelope[FrontendAccessCheckRead])
async def check_my_frontend_access(
    request: Request,
    payload: FrontendAccessCheckRequest,
) -> dict[str, object]:
    context = _request_context()
    tenant_id = getattr(context, "tenant_id", None)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        result = await FrontendAccessEvaluationService(session).check(
            client_id=payload.client_id,
            tenant_id=tenant_id,
            user_id=context.user_id,
            access_keys=payload.access_keys,
        )
        return ok(result)


def _session_factory(request: Request):
    return request.app.state.session_factory


def _active_session(session: AsyncSession | None) -> AsyncSession:
    if session is None:
        raise AppError("SYSTEM_ERROR", "Database session is not available", status_code=500)
    return session


def _permission_registry(request: Request) -> PermissionRegistry:
    registry = getattr(request.app.state, "permission_registry", None)
    if not isinstance(registry, PermissionRegistry):
        raise AppError("SYSTEM_ERROR", "Permission registry is not configured", status_code=500)
    return registry


def _event_publisher(request: Request, session: AsyncSession) -> OutboxEventPublisher:
    registry = getattr(request.app.state, "event_registry", None)
    if registry is not None and not isinstance(registry, EventRegistry):
        raise AppError("SYSTEM_ERROR", "Event registry is invalid", status_code=500)
    return OutboxEventPublisher(OutboxRepository(session, registry=registry))


def _audit_recorder(request: Request, session: AsyncSession) -> Any | None:
    registry = getattr(request.app.state, "app_registry", None)
    labels = {module.label for module in getattr(registry, "modules", [])}
    if "platform_audit" not in labels:
        return None
    public_api = importlib.import_module("platform_apps.audit.public_api")
    return public_api.AuditService(session)


def _request_context():
    context = get_current_context()
    if context is None or not context.user_id:
        raise AppError("AUTH_INVALID_TOKEN", "Authenticated user is required", status_code=401)
    return context


def _current_tenant_id(context: Any) -> str:
    tenant_id = getattr(context, "tenant_id", None)
    if not tenant_id:
        raise AppError("TENANT_ACCESS_DENIED", "Tenant context is required", status_code=403)
    return tenant_id


def _role_grant_read(grant: Any) -> dict[str, object]:
    return {
        "id": grant.id,
        "tenant_id": grant.tenant_id,
        "subject_type": grant.subject_type,
        "subject_id": grant.subject_id,
        "role_template_id": grant.role_template_id,
        "policy_version": grant.policy_version,
    }


def _role_template_read(template: Any) -> dict[str, object]:
    return {
        "id": template.id,
        "scope": template.scope,
        "name": template.name,
        "version": template.version,
        "permissions": list(template.permissions),
    }


def _projected_policy_read(policy: Any) -> dict[str, object]:
    return {
        "tenant_id": policy.tenant_id,
        "subject": policy.subject,
        "resource": policy.resource,
        "action": policy.action,
        "effect": policy.effect,
        "role_grant_id": policy.role_grant_id,
        "policy_version": policy.policy_version,
    }


def _permission_check_read(
    permission: str,
    allowed_permissions: set[tuple[str, str]],
) -> dict[str, object]:
    resource, separator, action = permission.rpartition(":")
    if not separator or not resource.strip() or not action.strip():
        raise AppError(
            "VALIDATION_ERROR",
            "Permission must use resource:action format",
            status_code=400,
            details={"permission": permission},
        )
    resolved_resource = resource.strip()
    resolved_action = action.strip()
    return {
        "permission": f"{resolved_resource}:{resolved_action}",
        "resource": resolved_resource,
        "action": resolved_action,
        "allowed": (resolved_resource, resolved_action) in allowed_permissions,
    }


def _frontend_access_mapping_read(mapping: Any) -> dict[str, object]:
    return {
        "id": mapping.id,
        "client_id": mapping.client_id,
        "access_key": mapping.access_key,
        "owner_module": mapping.owner_module,
        "evaluation_scope": mapping.evaluation_scope,
        "expression": dict(mapping.expression_json),
        "description": mapping.description,
        "status": mapping.status,
        "version": mapping.version,
        "updated_by": mapping.updated_by,
        "reason": mapping.reason,
    }


def _frontend_access_mapping_revision_read(revision: Any) -> dict[str, object]:
    return {
        "id": revision.id,
        "mapping_id": revision.mapping_id,
        "client_id": revision.client_id,
        "access_key": revision.access_key,
        "old_expression": revision.old_expression_json,
        "new_expression": revision.new_expression_json,
        "old_status": revision.old_status,
        "new_status": revision.new_status,
        "version": revision.version,
        "changed_by": revision.changed_by,
        "reason": revision.reason,
    }
