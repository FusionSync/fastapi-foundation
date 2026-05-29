from core.base import BaseSchema
from core.tenancy import TenantStatus


class TenantRead(BaseSchema):
    id: str
    name: str
    code: str
    status: TenantStatus
    deployment_mode: str


class TenantMemberRead(BaseSchema):
    id: str
    tenant_id: str
    user_id: str
    status: str


class TenantInvitationRead(BaseSchema):
    id: str
    tenant_id: str
    email: str
    role_template_id: str | None
    status: str
    invited_by_user_id: str
    accepted_by_user_id: str | None
