from __future__ import annotations

from dataclasses import dataclass, field

from core.context import get_current_context, set_current_context
from core.exceptions import AppError
from core.tenancy.lifecycle import (
    TenantOperation,
    TenantStatus,
    assert_tenant_operation_allowed,
)


@dataclass(frozen=True, slots=True)
class TenantMembership:
    tenant_id: str
    active: bool = True


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_id: str
    default_tenant_id: str | None = None
    memberships: tuple[TenantMembership, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class TenantRecord:
    tenant_id: str
    status: TenantStatus = "active"


def resolve_current_tenant(
    *,
    current_user: CurrentUser | None,
    token_tenant_id: str | None = None,
    header_tenant_id: str | None = None,
    tenant: TenantRecord | None = None,
    operation: TenantOperation = "read",
) -> str:
    if current_user is None:
        raise AppError(
            "AUTH_INVALID_TOKEN",
            "Authenticated user is required before tenant resolution",
            status_code=401,
        )

    selected_tenant_id = _select_tenant_id(
        token_tenant_id=token_tenant_id,
        header_tenant_id=header_tenant_id,
        default_tenant_id=current_user.default_tenant_id,
    )
    if selected_tenant_id is None:
        raise AppError(
            "TENANT_ACCESS_DENIED",
            "Tenant selection is required",
            status_code=403,
        )

    if not _has_active_membership(current_user, selected_tenant_id):
        raise AppError(
            "TENANT_ACCESS_DENIED",
            "Current user is not an active member of the tenant",
            status_code=403,
            details={"tenant_id": selected_tenant_id},
        )

    if tenant is None:
        raise AppError(
            "TENANT_ACCESS_DENIED",
            "Tenant record must be loaded before tenant resolution",
            status_code=403,
            details={"tenant_id": selected_tenant_id},
        )
    tenant_record = tenant
    if tenant_record.tenant_id != selected_tenant_id:
        raise AppError(
            "TENANT_CONTEXT_CONFLICT",
            "Resolved tenant does not match loaded tenant record",
            status_code=403,
            details={
                "selected_tenant_id": selected_tenant_id,
                "tenant_id": tenant_record.tenant_id,
            },
        )
    assert_tenant_operation_allowed(
        tenant_id=selected_tenant_id,
        status=tenant_record.status,
        operation=operation,
    )

    context = get_current_context()
    if context is not None:
        set_current_context(context.with_user(current_user.user_id).with_tenant(selected_tenant_id).freeze())
    return selected_tenant_id


def _select_tenant_id(
    *,
    token_tenant_id: str | None,
    header_tenant_id: str | None,
    default_tenant_id: str | None,
) -> str | None:
    if token_tenant_id and header_tenant_id and token_tenant_id != header_tenant_id:
        raise AppError(
            "TENANT_CONTEXT_CONFLICT",
            "Header tenant conflicts with token tenant",
            status_code=403,
            details={"token_tenant_id": token_tenant_id, "header_tenant_id": header_tenant_id},
        )
    return token_tenant_id or header_tenant_id or default_tenant_id


def _has_active_membership(current_user: CurrentUser, tenant_id: str) -> bool:
    return any(
        membership.tenant_id == tenant_id and membership.active
        for membership in current_user.memberships
    )
