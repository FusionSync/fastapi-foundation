from __future__ import annotations

from core.base import BaseSchema, CreateSchema


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


class RoleGrantRead(BaseSchema):
    id: str
    tenant_id: str
    subject_type: str
    subject_id: str
    role_template_id: str
    policy_version: int
