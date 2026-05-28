from __future__ import annotations

from dataclasses import dataclass

from core.exceptions import AppError

PLATFORM_TENANT_ID = "__platform__"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    tenant_id: str
    user_id: str
    resource: str
    action: str
    reason: str
    policy_version: int | None = None


def assert_platform_decision(decision: AuthorizationDecision) -> None:
    if not decision.allowed:
        raise AppError(
            "PERMISSION_DENIED",
            "Cross-tenant access requires an allowed platform decision",
            status_code=403,
            details={
                "user_id": decision.user_id,
                "resource": decision.resource,
                "action": decision.action,
                "reason": decision.reason,
            },
        )
    if decision.tenant_id != PLATFORM_TENANT_ID:
        raise AppError(
            "PERMISSION_DENIED",
            "Cross-tenant access requires a platform-scoped decision",
            status_code=403,
            details={
                "tenant_id": decision.tenant_id,
                "user_id": decision.user_id,
                "resource": decision.resource,
                "action": decision.action,
            },
        )
