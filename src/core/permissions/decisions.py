from __future__ import annotations

from collections.abc import Collection
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


def assert_authorization_decision(
    decision: AuthorizationDecision | None,
    *,
    tenant_id: str,
    actor_id: str,
    resource: str,
    actions: Collection[str],
    operation: str,
    allow_platform: bool = True,
) -> None:
    allowed_actions = set(actions)
    if not allowed_actions:
        raise ValueError("authorization decision actions cannot be empty")
    if decision is None:
        _raise_authorization_decision_denied(
            f"{operation} requires an authorization decision",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource=resource,
            actions=allowed_actions,
        )
    if not decision.allowed:
        _raise_authorization_decision_denied(
            f"{operation} requires an allowed authorization decision",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource=resource,
            actions=allowed_actions,
            reason=decision.reason,
        )
    if decision.user_id != actor_id:
        _raise_authorization_decision_denied(
            f"{operation} actor must match authorization decision user",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource=resource,
            actions=allowed_actions,
            reason=decision.reason,
        )
    allowed_tenant_ids = {tenant_id}
    if allow_platform:
        allowed_tenant_ids.add(PLATFORM_TENANT_ID)
    if decision.tenant_id not in allowed_tenant_ids:
        _raise_authorization_decision_denied(
            f"{operation} decision tenant does not match target tenant",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource=resource,
            actions=allowed_actions,
            reason=decision.reason,
        )
    if decision.resource != resource:
        _raise_authorization_decision_denied(
            f"{operation} requires {resource} permission",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource=resource,
            actions=allowed_actions,
            reason=decision.reason,
        )
    if decision.action not in allowed_actions:
        _raise_authorization_decision_denied(
            f"{operation} decision action is not sufficient",
            tenant_id=tenant_id,
            actor_id=actor_id,
            resource=resource,
            actions=allowed_actions,
            reason=decision.reason,
        )


def _raise_authorization_decision_denied(
    message: str,
    *,
    tenant_id: str,
    actor_id: str,
    resource: str,
    actions: Collection[str],
    reason: str | None = None,
) -> None:
    raise AppError(
        "PERMISSION_DENIED",
        message,
        status_code=403,
        details={
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "resource": resource,
            "allowed_actions": sorted(actions),
            "reason": reason,
        },
    )
