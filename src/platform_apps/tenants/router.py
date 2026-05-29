from typing import NoReturn

from core.base import create_router
from core.exceptions import AppError
from core.serialization import Envelope, ListEnvelope
from platform_apps.tenants.schemas import (
    TenantInvitationRead,
    TenantMemberRead,
    TenantRead,
)

platform_router = create_router(
    "/platform/tenants",
    tags=["platform-tenants"],
    tenant_required=False,
    permissions=["tenant:manage"],
    permission_scope="platform",
)
member_read_router = create_router(
    "/tenants/{tenant_id}/members",
    tags=["tenant-members"],
    permissions=["tenant_member:read"],
)
member_manage_router = create_router(
    "/tenants/{tenant_id}/members",
    tags=["tenant-members"],
    permissions=["tenant_member:manage"],
    tenant_operation="write",
)
invitation_issue_router = create_router(
    "/tenants/{tenant_id}/invitations",
    tags=["tenant-invitations"],
    permissions=["tenant_invitation:invite"],
    tenant_operation="write",
)
invitation_revoke_router = create_router(
    "/tenants/{tenant_id}/invitations",
    tags=["tenant-invitations"],
    permissions=["tenant_invitation:revoke"],
    tenant_operation="write",
)
invitation_accept_router = create_router(
    "/tenant-invitations",
    tags=["tenant-invitations"],
    tenant_required=False,
)

# Backward-compatible name for callers that imported the original single router.
router = platform_router


@platform_router.get("", response_model=ListEnvelope[TenantRead])
async def list_tenants() -> None:
    _tenant_http_not_connected()


@platform_router.post("", response_model=Envelope[TenantRead])
async def provision_tenant() -> None:
    _tenant_http_not_connected()


@member_read_router.get("", response_model=ListEnvelope[TenantMemberRead])
async def list_tenant_members(tenant_id: str) -> None:
    _tenant_http_not_connected()


@member_manage_router.post("", response_model=Envelope[TenantMemberRead])
async def create_tenant_member(tenant_id: str) -> None:
    _tenant_http_not_connected()


@member_manage_router.patch("/{member_id}", response_model=Envelope[TenantMemberRead])
async def update_tenant_member(
    tenant_id: str,
    member_id: str,
) -> None:
    _tenant_http_not_connected()


@invitation_issue_router.post("", response_model=Envelope[TenantInvitationRead])
async def issue_tenant_invitation(tenant_id: str) -> None:
    _tenant_http_not_connected()


@invitation_revoke_router.patch(
    "/{invitation_id}/revoke",
    response_model=Envelope[TenantInvitationRead],
)
async def revoke_tenant_invitation(
    tenant_id: str,
    invitation_id: str,
) -> None:
    _tenant_http_not_connected()


@invitation_accept_router.post("/accept", response_model=Envelope[TenantInvitationRead])
async def accept_tenant_invitation() -> None:
    _tenant_http_not_connected()


def _tenant_http_not_connected() -> NoReturn:
    raise AppError(
        "PLATFORM_TENANTS_HTTP_NOT_READY",
        "Platform tenants HTTP endpoint is declared for route protection only.",
        status_code=501,
    )
