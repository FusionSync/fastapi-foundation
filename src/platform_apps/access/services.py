from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditRecorder
from core.events import EventPublisher
from core.exceptions import AppError
from core.permissions import (
    PLATFORM_TENANT_ID,
    AuthorizationDecision,
    PermissionRegistry,
    PolicyProjector,
    ProjectedPolicy,
    ReconciliationResult,
    RoleGrantService,
    assert_authorization_decision,
)
from core.permissions.models import RoleGrant, RoleTemplate
from core.permissions.projector import ROLE_GRANT_CHANGED_EVENT


class AccessCatalogService:
    def __init__(self, permission_registry: PermissionRegistry) -> None:
        self.permission_registry = permission_registry

    def list_permissions(self) -> list[dict[str, object]]:
        return [
            permission.to_dict()
            for permission in sorted(
                self.permission_registry.permissions,
                key=lambda item: (
                    item.spec.scope,
                    item.app_label,
                    item.spec.resource,
                    item.spec.action,
                ),
            )
        ]


class RoleTemplateService:
    def __init__(
        self,
        session: AsyncSession,
        permission_registry: PermissionRegistry,
    ) -> None:
        self.session = session
        self.permission_registry = permission_registry

    async def list_templates(self, *, scope: str | None = None) -> list[RoleTemplate]:
        statement = select(RoleTemplate)
        if scope is not None:
            statement = statement.where(RoleTemplate.scope == _normalize_scope(scope))
        result = await self.session.execute(
            statement.order_by(RoleTemplate.scope.asc(), RoleTemplate.name.asc())
        )
        return list(result.scalars().all())

    async def create_template(
        self,
        *,
        scope: str,
        name: str,
        version: int,
        permissions: list[dict[str, str]],
    ) -> RoleTemplate:
        resolved_scope = _normalize_scope(scope)
        resolved_name = _normalize_name(name)
        _validate_version(version)
        resolved_permissions = self._validate_permissions(
            scope=resolved_scope,
            permissions=permissions,
        )
        existing = await self._template_by_identity(
            scope=resolved_scope,
            name=resolved_name,
            version=version,
        )
        if existing is not None:
            raise AppError(
                "CONFLICT",
                "RoleTemplate already exists for scope, name and version",
                status_code=409,
            )
        template = RoleTemplate(
            id=str(uuid4()),
            scope=resolved_scope,
            name=resolved_name,
            version=version,
            permissions=resolved_permissions,
        )
        self.session.add(template)
        await self.session.flush()
        return template

    async def update_template(
        self,
        template_id: str,
        *,
        name: str | None = None,
        permissions: list[dict[str, str]] | None = None,
    ) -> RoleTemplate:
        template = await self.session.get(RoleTemplate, template_id)
        if template is None:
            raise AppError("NOT_FOUND", f"RoleTemplate {template_id!r} not found", status_code=404)
        if name is not None:
            template.name = _normalize_name(name)
        if permissions is not None:
            template.permissions = self._validate_permissions(
                scope=template.scope,
                permissions=permissions,
            )
            template.version += 1
        await self.session.flush()
        return template

    async def _template_by_identity(
        self,
        *,
        scope: str,
        name: str,
        version: int,
    ) -> RoleTemplate | None:
        result = await self.session.execute(
            select(RoleTemplate)
            .where(RoleTemplate.scope == scope)
            .where(RoleTemplate.name == name)
            .where(RoleTemplate.version == version)
        )
        return result.scalars().first()

    def _validate_permissions(
        self,
        *,
        scope: str,
        permissions: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if not permissions:
            raise AppError(
                "VALIDATION_ERROR",
                "RoleTemplate requires at least one permission",
                status_code=400,
            )
        resolved: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for permission in permissions:
            resource = str(permission.get("resource", "")).strip()
            action = str(permission.get("action", "")).strip()
            if not resource or not action:
                raise AppError(
                    "VALIDATION_ERROR",
                    "RoleTemplate permission requires resource and action",
                    status_code=400,
                )
            if not self.permission_registry.has_permission(
                resource=resource,
                action=action,
                scope=scope,
            ):
                raise AppError(
                    "VALIDATION_ERROR",
                    "RoleTemplate permission is not registered",
                    status_code=400,
                    details={"scope": scope, "resource": resource, "action": action},
                )
            key = (resource, action)
            if key in seen:
                continue
            seen.add(key)
            resolved.append({"resource": resource, "action": action})
        return resolved


class TenantRoleGrantService:
    def __init__(
        self,
        session: AsyncSession,
        events: EventPublisher,
        *,
        permission_registry: PermissionRegistry | None = None,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.events = events
        self.permission_registry = permission_registry
        self.audit = audit

    async def list_grants(self, *, tenant_id: str) -> list[RoleGrant]:
        result = await self.session.execute(
            select(RoleGrant)
            .where(RoleGrant.tenant_id == tenant_id)
            .order_by(RoleGrant.subject_type.asc(), RoleGrant.subject_id.asc())
        )
        return list(result.scalars().all())

    async def grant_role(
        self,
        *,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
        role_template_id: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None,
        reason: str | None,
    ) -> RoleGrant:
        resolved_reason = _require_reason(reason, operation="Role grant")
        resolved_subject_type = _normalize_subject_type(subject_type)
        resolved_subject_id = _normalize_subject_id(subject_id)
        role_template = await self.session.get(RoleTemplate, role_template_id)
        if role_template is None:
            raise AppError(
                "NOT_FOUND",
                f"RoleTemplate {role_template_id!r} not found",
                status_code=404,
            )
        if role_template.scope != "tenant":
            raise AppError(
                "VALIDATION_ERROR",
                "Tenant role grants require a tenant role template",
                status_code=400,
            )
        existing = await self._existing_grant(
            tenant_id=tenant_id,
            subject_type=resolved_subject_type,
            subject_id=resolved_subject_id,
            role_template_id=role_template.id,
        )
        if existing is not None:
            raise AppError(
                "CONFLICT",
                "RoleGrant already exists for this subject and role template",
                status_code=409,
                details={
                    "grant_id": existing.id,
                    "tenant_id": tenant_id,
                    "subject_type": resolved_subject_type,
                    "subject_id": resolved_subject_id,
                    "role_template_id": role_template.id,
                },
            )
        grant = await RoleGrantService(self.session, self.events, audit=self.audit).grant_role(
            tenant_id=tenant_id,
            subject_type=resolved_subject_type,
            subject_id=resolved_subject_id,
            role_template_id=role_template.id,
            actor_id=actor_id,
            request_id=request_id,
            authorization_decision=authorization_decision,
            reason=resolved_reason,
            policy_version=role_template.version,
        )
        await self.session.flush()
        await self._project_grant(grant, role_template)
        return grant

    async def revoke_role(
        self,
        *,
        tenant_id: str,
        grant_id: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None,
        reason: str | None,
    ) -> RoleGrant:
        resolved_reason = _require_reason(reason, operation="Role revoke")
        grant = await self.session.get(RoleGrant, grant_id)
        if grant is None or grant.tenant_id != tenant_id:
            raise AppError("NOT_FOUND", f"RoleGrant {grant_id!r} not found", status_code=404)
        return await RoleGrantService(self.session, self.events, audit=self.audit).revoke_role(
            grant_id=grant_id,
            actor_id=actor_id,
            request_id=request_id,
            authorization_decision=authorization_decision,
            reason=resolved_reason,
        )

    async def _existing_grant(
        self,
        *,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
        role_template_id: str,
    ) -> RoleGrant | None:
        result = await self.session.execute(
            select(RoleGrant)
            .where(RoleGrant.tenant_id == tenant_id)
            .where(RoleGrant.subject_type == subject_type)
            .where(RoleGrant.subject_id == subject_id)
            .where(RoleGrant.role_template_id == role_template_id)
        )
        return result.scalars().first()

    async def _project_grant(self, grant: RoleGrant, role_template: RoleTemplate) -> None:
        await PolicyProjector(
            self.session,
            permission_registry=self.permission_registry,
        ).project_grant(grant, role_template)


class EffectiveAccessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_effective_permissions(
        self,
        *,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
    ) -> list[ProjectedPolicy]:
        subject = f"{_normalize_subject_type(subject_type)}:{_normalize_subject_id(subject_id)}"
        result = await self.session.execute(
            select(ProjectedPolicy)
            .where(ProjectedPolicy.tenant_id == tenant_id)
            .where(ProjectedPolicy.subject == subject)
            .order_by(ProjectedPolicy.resource.asc(), ProjectedPolicy.action.asc())
        )
        return list(result.scalars().all())


class AccessProjectionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        permission_registry: PermissionRegistry | None = None,
    ) -> None:
        self.session = session
        self.permission_registry = permission_registry

    async def reconcile(self, *, repair: bool) -> ReconciliationResult:
        return await PolicyProjector(
            self.session,
            permission_registry=self.permission_registry,
        ).reconcile(repair=repair)


class PlatformAdminService:
    def __init__(
        self,
        session: AsyncSession,
        events: EventPublisher,
        *,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.events = events
        self.audit = audit

    async def grant_platform_admin(
        self,
        *,
        user_id: str,
        role_template_id: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None = None,
        reason: str | None = None,
    ) -> RoleGrant:
        resolved_reason = _require_reason(reason, operation="Platform administrator grant")
        _assert_platform_admin_mutation_authorized(
            authorization_decision=authorization_decision,
            actor_id=actor_id,
        )
        role_template = await self.session.get(RoleTemplate, role_template_id)
        if role_template is None:
            raise AppError(
                "NOT_FOUND",
                f"RoleTemplate {role_template_id!r} not found",
                status_code=404,
            )
        if role_template.scope != "platform":
            raise AppError(
                "VALIDATION_ERROR",
                "Platform administrator grants require a platform role template",
                status_code=400,
            )

        current = await self._existing_platform_admin_grant(
            user_id=user_id,
            role_template_id=role_template_id,
        )
        if current is not None:
            return current

        grant = RoleGrant(
            id=str(uuid4()),
            tenant_id=PLATFORM_TENANT_ID,
            subject_type="user",
            subject_id=user_id,
            role_template_id=role_template_id,
            policy_version=authorization_decision.policy_version or role_template.version,
        )
        self.session.add(grant)
        await self.session.flush()
        await self.events.publish(
            event_type=ROLE_GRANT_CHANGED_EVENT,
            aggregate_type="role_grant",
            aggregate_id=grant.id,
            tenant_id=PLATFORM_TENANT_ID,
            payload={
                "tenant_id": PLATFORM_TENANT_ID,
                "actor_id": actor_id,
                "request_id": request_id,
                "grant_id": grant.id,
                "subject_type": grant.subject_type,
                "subject_id": grant.subject_id,
                "role_template_id": role_template_id,
                "reason": resolved_reason,
            },
        )
        if self.audit is not None:
            await self.audit.record(
                action="platform_admin.granted",
                resource_type="role_grant",
                resource_id=grant.id,
                result="success",
                tenant_id=PLATFORM_TENANT_ID,
                actor_id=actor_id,
                reason=resolved_reason,
                policy_version=grant.policy_version,
                request_id=request_id,
                payload={
                    "subject_type": grant.subject_type,
                    "subject_id": grant.subject_id,
                    "role_template_id": role_template_id,
                },
            )
        return grant

    async def _existing_platform_admin_grant(
        self,
        *,
        user_id: str,
        role_template_id: str,
    ) -> RoleGrant | None:
        result = await self.session.execute(
            select(RoleGrant)
            .where(RoleGrant.tenant_id == PLATFORM_TENANT_ID)
            .where(RoleGrant.subject_type == "user")
            .where(RoleGrant.subject_id == user_id)
            .where(RoleGrant.role_template_id == role_template_id)
        )
        return result.scalars().first()


def _assert_platform_admin_mutation_authorized(
    *,
    authorization_decision: AuthorizationDecision | None,
    actor_id: str,
) -> None:
    assert_authorization_decision(
        authorization_decision,
        tenant_id=PLATFORM_TENANT_ID,
        actor_id=actor_id,
        resource="access.platform_admin",
        actions={"manage"},
        operation="Platform administrator grant",
        allow_platform=True,
    )


def _normalize_scope(value: str) -> str:
    resolved = value.strip()
    if resolved not in {"platform", "tenant"}:
        raise AppError(
            "VALIDATION_ERROR",
            "RoleTemplate scope must be platform or tenant",
            status_code=400,
        )
    return resolved


def _normalize_name(value: str) -> str:
    resolved = value.strip()
    if not resolved:
        raise AppError("VALIDATION_ERROR", "RoleTemplate name is required", status_code=400)
    return resolved


def _validate_version(value: int) -> None:
    if value < 1:
        raise AppError(
            "VALIDATION_ERROR",
            "RoleTemplate version must be greater than zero",
            status_code=400,
        )


def _normalize_subject_type(value: str) -> str:
    resolved = value.strip()
    if resolved not in {"user", "service_account"}:
        raise AppError(
            "VALIDATION_ERROR",
            "RoleGrant subject_type must be user or service_account",
            status_code=400,
        )
    return resolved


def _normalize_subject_id(value: str) -> str:
    resolved = value.strip()
    if not resolved:
        raise AppError("VALIDATION_ERROR", "RoleGrant subject_id is required", status_code=400)
    return resolved


def _require_reason(value: str | None, *, operation: str) -> str:
    if value is None or not value.strip():
        raise AppError(
            "VALIDATION_ERROR",
            f"{operation} reason is required",
            status_code=400,
        )
    return value.strip()


__all__ = [
    "AccessCatalogService",
    "AccessProjectionService",
    "EffectiveAccessService",
    "PlatformAdminService",
    "RoleTemplateService",
    "TenantRoleGrantService",
]
