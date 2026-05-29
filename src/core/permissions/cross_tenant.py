from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from core.exceptions import AppError
from core.permissions.authorization import AuthorizationService
from core.permissions.decisions import AuthorizationDecision, assert_platform_decision


@dataclass(frozen=True, slots=True)
class CrossTenantPermission:
    decision: AuthorizationDecision
    reason: str
    target_tenant_ids: tuple[str, ...]
    resource: str
    action: str
    request_id: str | None = None
    resource_id: str | None = None

    @property
    def platform_decision(self) -> AuthorizationDecision:
        return self.decision


class CrossTenantPermissionGate:
    def __init__(self, authorization: AuthorizationService) -> None:
        self.authorization = authorization

    async def require(
        self,
        *,
        user_id: str,
        resource: str,
        action: str,
        target_tenant_ids: Sequence[str],
        reason: str,
        resource_id: str | None = None,
        request_id: str | None = None,
    ) -> CrossTenantPermission:
        targets = _validate_cross_tenant_targets(target_tenant_ids)
        _validate_cross_tenant_reason(reason)
        decision = await self.authorization.require_platform(
            user_id=user_id,
            resource=resource,
            action=action,
            resource_id=resource_id,
            request_id=request_id,
        )
        access = CrossTenantPermission(
            decision=decision,
            reason=reason.strip(),
            target_tenant_ids=targets,
            resource=resource,
            action=action,
            request_id=request_id,
            resource_id=resource_id,
        )
        assert_cross_tenant_permission(
            access,
            resource=resource,
            actions={action},
        )
        return access


def assert_cross_tenant_permission(
    access: CrossTenantPermission,
    *,
    resource: str,
    actions: Collection[str],
    target_tenant_id: str | None = None,
    actor_id: str | None = None,
) -> None:
    if not actions:
        raise ValueError("cross-tenant permission actions cannot be empty")
    _validate_cross_tenant_reason(access.reason)
    _validate_cross_tenant_targets(access.target_tenant_ids)
    assert_platform_decision(access.decision)
    if actor_id is not None and access.decision.user_id != actor_id:
        _deny_cross_tenant_access(
            "Cross-tenant access actor must match platform decision user",
            access,
            resource=resource,
            actions=actions,
            target_tenant_id=target_tenant_id,
        )
    if access.resource != resource or access.decision.resource != resource:
        _deny_cross_tenant_access(
            "Cross-tenant access requires matching platform resource",
            access,
            resource=resource,
            actions=actions,
            target_tenant_id=target_tenant_id,
        )
    if access.action not in actions or access.decision.action not in actions:
        _deny_cross_tenant_access(
            "Cross-tenant access platform action is not sufficient",
            access,
            resource=resource,
            actions=actions,
            target_tenant_id=target_tenant_id,
        )
    if target_tenant_id is not None and target_tenant_id not in access.target_tenant_ids:
        _deny_cross_tenant_access(
            "Cross-tenant access target tenant is not in authorized scope",
            access,
            resource=resource,
            actions=actions,
            target_tenant_id=target_tenant_id,
        )


def cross_tenant_reason_and_decision(
    *,
    reason: str | None = None,
    platform_decision: AuthorizationDecision | None = None,
    platform_access: CrossTenantPermission | None = None,
    resource: str = "cross_tenant",
    actions: Collection[str] = frozenset({"read"}),
    target_tenant_id: str | None = None,
) -> tuple[str, AuthorizationDecision]:
    if platform_access is not None:
        assert_cross_tenant_permission(
            platform_access,
            resource=resource,
            actions=actions,
            target_tenant_id=target_tenant_id,
        )
        return platform_access.reason, platform_access.decision
    if platform_decision is None:
        raise AppError(
            "PERMISSION_DENIED",
            "Cross-tenant access requires a platform permission gate",
            status_code=403,
            details={"resource": resource, "allowed_actions": sorted(actions)},
        )
    _validate_cross_tenant_reason(reason or "")
    assert_platform_decision(platform_decision)
    return (reason or "").strip(), platform_decision


def _validate_cross_tenant_targets(target_tenant_ids: Sequence[str]) -> tuple[str, ...]:
    targets = tuple(tenant_id.strip() for tenant_id in target_tenant_ids if tenant_id.strip())
    if not targets:
        raise AppError(
            "PERMISSION_DENIED",
            "Cross-tenant access requires at least one target tenant",
            status_code=403,
            details={"reason": "missing_target_tenant"},
        )
    return targets


def _validate_cross_tenant_reason(reason: str) -> None:
    if not reason.strip():
        raise AppError(
            "PERMISSION_DENIED",
            "Cross-tenant access requires an audit reason",
            status_code=403,
            details={"reason": "missing_audit_reason"},
        )


def _deny_cross_tenant_access(
    message: str,
    access: CrossTenantPermission,
    *,
    resource: str,
    actions: Collection[str],
    target_tenant_id: str | None,
) -> None:
    raise AppError(
        "PERMISSION_DENIED",
        message,
        status_code=403,
        details={
            "tenant_id": access.decision.tenant_id,
            "user_id": access.decision.user_id,
            "resource": resource,
            "allowed_actions": sorted(actions),
            "action": access.action,
            "target_tenant_id": target_tenant_id,
            "reason": access.reason,
        },
    )
