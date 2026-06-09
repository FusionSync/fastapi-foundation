from __future__ import annotations

from pydantic import Field

from core.base import BaseSchema, CreateSchema, UpdateSchema


class PermissionRead(BaseSchema):
    app_label: str
    resource: str
    action: str
    scope: str
    description: str
    risk_level: str


class PlatformAdminGrantRequest(CreateSchema):
    user_id: str
    role_template_id: str
    reason: str | None = None


class RoleTemplatePermission(BaseSchema):
    resource: str
    action: str


class RoleTemplateCreateRequest(CreateSchema):
    scope: str
    name: str
    version: int = 1
    permissions: list[RoleTemplatePermission]


class RoleTemplateUpdateRequest(UpdateSchema):
    name: str | None = None
    permissions: list[RoleTemplatePermission] | None = None


class RoleTemplateRead(BaseSchema):
    id: str
    scope: str
    name: str
    version: int
    permissions: list[RoleTemplatePermission]


class TenantRoleGrantCreateRequest(CreateSchema):
    subject_type: str = "user"
    subject_id: str
    role_template_id: str
    reason: str | None = None


class RoleGrantRead(BaseSchema):
    id: str
    tenant_id: str
    subject_type: str
    subject_id: str
    role_template_id: str
    policy_version: int


class EffectivePermissionRead(BaseSchema):
    tenant_id: str
    subject: str
    resource: str
    action: str
    effect: str
    role_grant_id: str
    policy_version: int


class PermissionCheckRequest(CreateSchema):
    permissions: list[str] = Field(min_length=1)


class PermissionCheckItemRead(BaseSchema):
    permission: str
    resource: str
    action: str
    allowed: bool


class PermissionCheckRead(BaseSchema):
    permissions: list[PermissionCheckItemRead]


class ProjectionReconcileRequest(CreateSchema):
    repair: bool = False


class ProjectionReconcileRead(BaseSchema):
    ok: bool
    repaired: bool
    missing: list[EffectivePermissionRead]
    stale: list[EffectivePermissionRead]
