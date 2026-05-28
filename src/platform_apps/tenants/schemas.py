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
