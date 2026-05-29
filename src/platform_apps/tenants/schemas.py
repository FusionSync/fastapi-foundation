from datetime import datetime
from typing import ClassVar

from core.base import BaseSchema, CreateSchema, ListQuerySchema, UpdateSchema
from core.tenancy import TenantStatus


class TenantListQuery(ListQuerySchema):
    sortable_fields: ClassVar[frozenset[str] | None] = frozenset(
        {"created_at", "code", "name", "status"}
    )
    filterable_fields: ClassVar[frozenset[str] | None] = frozenset(
        {"keyword", "status"}
    )
    default_sort: ClassVar[tuple[str, ...]] = ("-created_at",)

    status: TenantStatus | None = None


class TenantRead(BaseSchema):
    id: str
    name: str
    code: str
    status: TenantStatus
    deployment_mode: str


class TenantCreateRequest(CreateSchema):
    id: str
    name: str
    code: str
    owner_user_id: str
    deployment_mode: str = "local"


class TenantMemberListQuery(ListQuerySchema):
    sortable_fields: ClassVar[frozenset[str] | None] = frozenset(
        {"created_at", "status", "user_id"}
    )
    filterable_fields: ClassVar[frozenset[str] | None] = frozenset(
        {"keyword", "status", "user_id"}
    )
    default_sort: ClassVar[tuple[str, ...]] = ("created_at",)

    status: str | None = None
    user_id: str | None = None


class TenantMemberRead(BaseSchema):
    id: str
    tenant_id: str
    user_id: str
    status: str


class TenantMemberCreateRequest(CreateSchema):
    user_id: str
    status: str = "active"


class TenantMemberUpdateRequest(UpdateSchema):
    status: str


class TenantInvitationRead(BaseSchema):
    id: str
    tenant_id: str
    email: str
    role_template_id: str | None
    status: str
    expires_at: datetime
    invited_by_user_id: str
    accepted_by_user_id: str | None


class TenantInvitationIssuedRead(TenantInvitationRead):
    token: str


class TenantInvitationIssueRequest(CreateSchema):
    email: str
    expires_at: datetime
    role_template_id: str | None = None


class TenantInvitationAcceptRequest(CreateSchema):
    token: str
    email: str
