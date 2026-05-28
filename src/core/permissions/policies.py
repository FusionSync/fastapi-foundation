from __future__ import annotations

from dataclasses import dataclass, field

from core.exceptions import AppError
from core.permissions.models import ProjectedPolicy, RoleGrant, RoleTemplate
from core.permissions.registry import PermissionRegistry


@dataclass(frozen=True, slots=True)
class PolicyRule:
    tenant_id: str
    subject: str
    resource: str
    action: str
    role_grant_id: str
    policy_version: int
    effect: str = "allow"

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (
            self.tenant_id,
            self.subject,
            self.resource,
            self.action,
            self.role_grant_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "subject": self.subject,
            "resource": self.resource,
            "action": self.action,
            "effect": self.effect,
            "role_grant_id": self.role_grant_id,
            "policy_version": self.policy_version,
        }


@dataclass(slots=True)
class ReconciliationResult:
    repaired: bool
    missing: list[PolicyRule] = field(default_factory=list)
    stale: list[PolicyRule] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.stale

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "repaired": self.repaired,
            "missing": [rule.to_dict() for rule in self.missing],
            "stale": [rule.to_dict() for rule in self.stale],
        }


def rules_for_grant(
    grant: RoleGrant,
    role_template: RoleTemplate,
    *,
    permission_registry: PermissionRegistry | None = None,
) -> list[PolicyRule]:
    subject = f"{grant.subject_type}:{grant.subject_id}"
    _validate_role_template_permissions(role_template, permission_registry=permission_registry)
    return [
        PolicyRule(
            tenant_id=grant.tenant_id,
            subject=subject,
            resource=permission["resource"],
            action=permission["action"],
            role_grant_id=grant.id,
            policy_version=grant.policy_version,
        )
        for permission in role_template.permissions
    ]


def _validate_role_template_permissions(
    role_template: RoleTemplate,
    *,
    permission_registry: PermissionRegistry | None,
) -> None:
    if permission_registry is None:
        return
    for permission in role_template.permissions:
        resource = permission.get("resource")
        action = permission.get("action")
        if not isinstance(resource, str) or not isinstance(action, str):
            raise AppError(
                "VALIDATION_ERROR",
                "RoleTemplate permission must include resource and action",
                status_code=400,
                details={"role_template_id": role_template.id},
            )
        if permission_registry.has_permission(
            resource=resource,
            action=action,
            scope=role_template.scope,
        ):
            continue
        raise AppError(
            "VALIDATION_ERROR",
            "RoleTemplate permission is not registered",
            status_code=400,
            details={
                "resource": resource,
                "action": action,
                "scope": role_template.scope,
            },
        )


def rule_from_policy(policy: ProjectedPolicy) -> PolicyRule:
    return PolicyRule(
        tenant_id=policy.tenant_id,
        subject=policy.subject,
        resource=policy.resource,
        action=policy.action,
        effect=policy.effect,
        role_grant_id=policy.role_grant_id,
        policy_version=policy.policy_version,
    )


def projected_policy_from_rule(rule: PolicyRule) -> ProjectedPolicy:
    return ProjectedPolicy(
        tenant_id=rule.tenant_id,
        subject=rule.subject,
        resource=rule.resource,
        action=rule.action,
        effect=rule.effect,
        role_grant_id=rule.role_grant_id,
        policy_version=rule.policy_version,
    )
