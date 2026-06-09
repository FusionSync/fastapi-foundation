from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.permissions import PLATFORM_TENANT_ID, PermissionRegistry, PolicyProjector
from core.permissions.models import ProjectedPolicy, RoleGrant, RoleTemplate

DEFAULT_PLATFORM_ADMIN_TEMPLATE_ID = "platform-admin"


@dataclass(frozen=True, slots=True)
class PlatformAdminBootstrapResult:
    grant: RoleGrant
    role_template: RoleTemplate
    projected_permissions: int

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "tenant_id": self.grant.tenant_id,
            "subject_type": self.grant.subject_type,
            "subject_id": self.grant.subject_id,
            "role_template_id": self.role_template.id,
            "grant_id": self.grant.id,
            "projected_permissions": self.projected_permissions,
        }


class PlatformAdminBootstrapService:
    def __init__(
        self,
        session: AsyncSession,
        permission_registry: PermissionRegistry,
    ) -> None:
        self.session = session
        self.permission_registry = permission_registry

    async def bootstrap_first_admin(
        self,
        *,
        user_id: str,
        role_template_id: str = DEFAULT_PLATFORM_ADMIN_TEMPLATE_ID,
        template_name: str = "platform-admin",
        reason: str = "initial platform admin bootstrap",
    ) -> PlatformAdminBootstrapResult:
        resolved_user_id = _required_text(user_id, field="user_id")
        resolved_role_template_id = _required_text(role_template_id, field="role_template_id")
        resolved_template_name = _required_text(template_name, field="template_name")
        _required_text(reason, field="reason")
        existing_platform_grants = int(
            await self.session.scalar(
                select(func.count())
                .select_from(RoleGrant)
                .where(RoleGrant.tenant_id == PLATFORM_TENANT_ID)
            )
            or 0
        )
        if existing_platform_grants:
            raise AppError(
                "CONFLICT",
                "platform administrator bootstrap is already completed",
                status_code=409,
                details={"platform_grant_count": existing_platform_grants},
            )

        role_template = await self._role_template(
            role_template_id=resolved_role_template_id,
            template_name=resolved_template_name,
        )
        grant = RoleGrant(
            id=str(uuid4()),
            tenant_id=PLATFORM_TENANT_ID,
            subject_type="user",
            subject_id=resolved_user_id,
            role_template_id=role_template.id,
            policy_version=role_template.version,
        )
        self.session.add(grant)
        await self.session.flush()
        rules = await PolicyProjector(
            self.session,
            permission_registry=self.permission_registry,
        ).project_grant(grant, role_template)
        projected_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(ProjectedPolicy)
                .where(ProjectedPolicy.role_grant_id == grant.id)
            )
            or len(rules)
        )
        return PlatformAdminBootstrapResult(
            grant=grant,
            role_template=role_template,
            projected_permissions=projected_count,
        )

    async def _role_template(
        self,
        *,
        role_template_id: str,
        template_name: str,
    ) -> RoleTemplate:
        current = await self.session.get(RoleTemplate, role_template_id)
        if current is not None:
            if current.scope != "platform":
                raise AppError(
                    "VALIDATION_ERROR",
                    "platform admin bootstrap requires a platform role template",
                    status_code=400,
                    details={"role_template_id": role_template_id, "scope": current.scope},
                )
            return current

        permissions = _platform_permissions(self.permission_registry)
        if not permissions:
            raise AppError(
                "VALIDATION_ERROR",
                "platform admin bootstrap requires at least one platform permission",
                status_code=400,
            )
        role_template = RoleTemplate(
            id=role_template_id,
            scope="platform",
            name=template_name,
            version=1,
            permissions=permissions,
        )
        self.session.add(role_template)
        await self.session.flush()
        return role_template


def _platform_permissions(permission_registry: PermissionRegistry) -> list[dict[str, str]]:
    permissions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for permission in sorted(
        permission_registry.permissions,
        key=lambda item: (item.spec.resource, item.spec.action),
    ):
        if permission.spec.scope != "platform":
            continue
        key = (permission.spec.resource, permission.spec.action)
        if key in seen:
            continue
        seen.add(key)
        permissions.append({"resource": permission.spec.resource, "action": permission.spec.action})
    return permissions


def _required_text(value: str, *, field: str) -> str:
    resolved = value.strip()
    if not resolved:
        raise AppError("VALIDATION_ERROR", f"{field} is required", status_code=400)
    return resolved


__all__ = [
    "DEFAULT_PLATFORM_ADMIN_TEMPLATE_ID",
    "PlatformAdminBootstrapResult",
    "PlatformAdminBootstrapService",
]
