import pytest

from core.config import Settings
from core.context import (
    RequestContext,
    get_current_context,
    reset_current_context,
    set_current_context,
)
from core.exceptions import AppError
from core.tenancy import (
    CurrentUser,
    TenantMembership,
    TenantRecord,
    resolve_current_tenant,
    tenant_lifecycle_policy_from_settings,
)


def test_resolve_current_tenant_injects_frozen_context() -> None:
    token = set_current_context(RequestContext(request_id="req_test"))
    try:
        tenant_id = resolve_current_tenant(
            current_user=_user("user-1", "tenant-a"),
            header_tenant_id="tenant-a",
            tenant=TenantRecord(tenant_id="tenant-a", status="active"),
        )

        context = get_current_context()
        assert tenant_id == "tenant-a"
        assert context is not None
        assert context.user_id == "user-1"
        assert context.tenant_id == "tenant-a"
        assert context.frozen is True
    finally:
        reset_current_context(token)


def test_header_and_token_tenant_conflict_is_rejected() -> None:
    with pytest.raises(AppError) as exc_info:
        resolve_current_tenant(
            current_user=_user("user-1", "tenant-a"),
            token_tenant_id="tenant-a",
            header_tenant_id="tenant-b",
        )

    assert exc_info.value.code == "TENANT_CONTEXT_CONFLICT"


def test_inactive_membership_is_rejected() -> None:
    user = CurrentUser(
        user_id="user-1",
        memberships=(TenantMembership(tenant_id="tenant-a", active=False),),
    )

    with pytest.raises(AppError) as exc_info:
        resolve_current_tenant(current_user=user, header_tenant_id="tenant-a")

    assert exc_info.value.code == "TENANT_ACCESS_DENIED"


def test_tenant_resolution_requires_loaded_tenant_record() -> None:
    with pytest.raises(AppError) as exc_info:
        resolve_current_tenant(
            current_user=_user("user-1", "tenant-a"),
            header_tenant_id="tenant-a",
        )

    assert exc_info.value.code == "TENANT_ACCESS_DENIED"


def test_suspended_tenant_allows_read_but_rejects_write() -> None:
    user = _user("user-1", "tenant-a")
    suspended = TenantRecord(tenant_id="tenant-a", status="suspended")

    assert (
        resolve_current_tenant(
            current_user=user,
            header_tenant_id="tenant-a",
            tenant=suspended,
            operation="read",
        )
        == "tenant-a"
    )
    with pytest.raises(AppError) as exc_info:
        resolve_current_tenant(
            current_user=user,
            header_tenant_id="tenant-a",
            tenant=suspended,
            operation="write",
        )

    assert exc_info.value.code == "TENANT_STATE_FORBIDDEN"


def test_archived_read_can_be_enabled_from_settings_policy() -> None:
    policy = tenant_lifecycle_policy_from_settings(
        Settings(tenant_lifecycle={"allow_archived_read": True})
    )

    tenant_id = resolve_current_tenant(
        current_user=_user("user-1", "tenant-a"),
        header_tenant_id="tenant-a",
        tenant=TenantRecord(tenant_id="tenant-a", status="archived"),
        operation="read",
        policy=policy,
    )

    assert tenant_id == "tenant-a"


def _user(user_id: str, tenant_id: str) -> CurrentUser:
    return CurrentUser(
        user_id=user_id,
        default_tenant_id=tenant_id,
        memberships=(TenantMembership(tenant_id=tenant_id),),
    )
