from __future__ import annotations

from typing import Literal

from pydantic import Field

from core.base import CreateSchema, Schema, UpdateSchema

FrontendAccessScope = Literal["tenant", "platform"]
FrontendAccessStatus = Literal["active", "disabled", "deprecated"]
FrontendAccessReason = Literal[
    "matched_expression",
    "missing_permission",
    "unknown_access_key",
    "disabled_mapping",
    "invalid_mapping",
    "tenant_context_required",
]


class PermissionRead(Schema):
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


class RoleTemplatePermission(Schema):
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


class RoleTemplateRead(Schema):
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


class RoleGrantRead(Schema):
    id: str
    tenant_id: str
    subject_type: str
    subject_id: str
    role_template_id: str
    policy_version: int


class EffectivePermissionRead(Schema):
    tenant_id: str
    subject: str
    resource: str
    action: str
    effect: str
    role_grant_id: str
    policy_version: int


class PermissionCheckRequest(CreateSchema):
    permissions: list[str] = Field(min_length=1)


class PermissionCheckItemRead(Schema):
    permission: str
    resource: str
    action: str
    allowed: bool


class PermissionCheckRead(Schema):
    permissions: list[PermissionCheckItemRead]


class ProjectionReconcileRequest(CreateSchema):
    repair: bool = False


class ProjectionReconcileRead(Schema):
    ok: bool
    repaired: bool
    missing: list[EffectivePermissionRead]
    stale: list[EffectivePermissionRead]


class FrontendAccessMappingCreateRequest(CreateSchema):
    client_id: str = Field(min_length=1, max_length=64)
    access_key: str = Field(min_length=1, max_length=160)
    owner_module: str = Field(min_length=1, max_length=64)
    evaluation_scope: FrontendAccessScope
    expression: dict[str, object]
    description: str | None = Field(default=None, max_length=512)
    reason: str | None = None


class FrontendAccessMappingUpdateRequest(UpdateSchema):
    owner_module: str | None = Field(default=None, min_length=1, max_length=64)
    evaluation_scope: FrontendAccessScope | None = None
    expression: dict[str, object] | None = None
    description: str | None = Field(default=None, max_length=512)
    status: FrontendAccessStatus | None = None
    reason: str | None = None


class FrontendAccessMappingRead(Schema):
    id: str
    client_id: str
    access_key: str
    owner_module: str
    evaluation_scope: FrontendAccessScope
    expression: dict[str, object]
    description: str | None
    status: FrontendAccessStatus
    version: int
    updated_by: str | None
    reason: str | None


class FrontendAccessMappingRevisionRead(Schema):
    id: str
    mapping_id: str
    client_id: str
    access_key: str
    old_expression: dict[str, object] | None
    new_expression: dict[str, object] | None
    old_status: str | None
    new_status: str | None
    version: int
    changed_by: str
    reason: str


class FrontendAccessValidateRequest(CreateSchema):
    evaluation_scope: FrontendAccessScope
    expression: dict[str, object]
    reason: str | None = None


class FrontendAccessValidateRead(Schema):
    ok: bool
    permissions: list[str]


class FrontendAccessCheckRequest(CreateSchema):
    client_id: str = Field(default="console-web", min_length=1, max_length=64)
    access_keys: list[str] = Field(min_length=1, max_length=100)


class FrontendAccessCheckItemRead(Schema):
    access_key: str
    allowed: bool
    reason: FrontendAccessReason
    version: int | None = None


class FrontendAccessCheckRead(Schema):
    client_id: str
    tenant_id: str | None
    policy_version: int
    access_revision: str
    evaluated_at: str
    results: list[FrontendAccessCheckItemRead]


class FrontendAccessRead(Schema):
    client_id: str
    tenant_id: str | None
    version: int
    policy_version: int
    access_revision: str
    evaluated_at: str
    permissions: list[str]
    access: dict[str, bool]
