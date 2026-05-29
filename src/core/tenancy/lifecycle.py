from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from core.exceptions import AppError

TenantStatus = Literal[
    "provisioning",
    "active",
    "suspended",
    "deleting",
    "archived",
    "deleted",
]
TenantOperation = Literal[
    "login",
    "read",
    "write",
    "task",
    "file_download",
    "background_cleanup",
    "admin",
]
SessionRevocationHook = Callable[[str, str], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class TenantLifecyclePolicy:
    allow_suspended_file_download: bool = False
    allow_archived_read: bool = False
    allow_archived_file_download: bool = False


ALLOWED_TRANSITIONS: dict[TenantStatus, set[TenantStatus]] = {
    "provisioning": {"active", "deleted"},
    "active": {"suspended", "deleting"},
    "suspended": {"active", "deleting"},
    "deleting": {"archived", "deleted"},
    "archived": {"deleted"},
    "deleted": set(),
}


def assert_tenant_operation_allowed(
    *,
    tenant_id: str,
    status: TenantStatus,
    operation: TenantOperation,
    policy: TenantLifecyclePolicy | None = None,
) -> None:
    if is_tenant_operation_allowed(status, operation, policy=policy):
        return
    raise AppError(
        "TENANT_STATE_FORBIDDEN",
        "Tenant state does not allow this operation",
        status_code=403,
        details={"tenant_id": tenant_id, "status": status, "operation": operation},
    )


def is_tenant_operation_allowed(
    status: TenantStatus,
    operation: TenantOperation,
    *,
    policy: TenantLifecyclePolicy | None = None,
) -> bool:
    resolved_policy = policy or TenantLifecyclePolicy()
    if operation == "admin":
        return True
    if status == "active":
        return True
    if status == "suspended":
        if operation in {"login", "read"}:
            return True
        if operation == "file_download":
            return resolved_policy.allow_suspended_file_download
        return False
    if status == "deleting":
        return operation == "background_cleanup"
    if status == "archived":
        if operation == "read":
            return resolved_policy.allow_archived_read
        if operation == "file_download":
            return resolved_policy.allow_archived_file_download
        return False
    return False


def validate_tenant_transition(current: TenantStatus, target: TenantStatus) -> None:
    if target in ALLOWED_TRANSITIONS[current]:
        return
    raise AppError(
        "TENANT_STATE_FORBIDDEN",
        f"Invalid tenant lifecycle transition: {current} -> {target}",
        status_code=409,
        details={"current": current, "target": target},
    )
