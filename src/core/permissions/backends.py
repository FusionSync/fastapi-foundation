from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.permissions.cache import DistributedPermissionCache
from core.permissions.models import ProjectedPolicy
from core.permissions.policies import PolicyRule


@dataclass(frozen=True, slots=True)
class PolicyMatch:
    tenant_id: str
    subject: str
    resource: str
    action: str
    role_grant_id: str
    policy_version: int
    effect: str = "allow"
    reason: str = "matched_projected_policy"

    @classmethod
    def from_rule(cls, rule: PolicyRule, *, reason: str) -> PolicyMatch:
        return cls(
            tenant_id=rule.tenant_id,
            subject=rule.subject,
            resource=rule.resource,
            action=rule.action,
            effect=rule.effect,
            role_grant_id=rule.role_grant_id,
            policy_version=rule.policy_version,
            reason=reason,
        )

    @classmethod
    def from_projected_policy(cls, policy: ProjectedPolicy) -> PolicyMatch:
        return cls(
            tenant_id=policy.tenant_id,
            subject=policy.subject,
            resource=policy.resource,
            action=policy.action,
            effect=policy.effect,
            role_grant_id=policy.role_grant_id,
            policy_version=policy.policy_version,
            reason="matched_projected_policy",
        )

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> PolicyMatch | None:
        tenant_id = value.get("tenant_id")
        subject = value.get("subject")
        resource = value.get("resource")
        action = value.get("action")
        effect = value.get("effect")
        role_grant_id = value.get("role_grant_id")
        policy_version = value.get("policy_version")
        reason = value.get("reason")
        if not all(
            isinstance(item, str)
            for item in (tenant_id, subject, resource, action, effect, role_grant_id, reason)
        ):
            return None
        if not isinstance(policy_version, int):
            return None
        return cls(
            tenant_id=tenant_id,
            subject=subject,
            resource=resource,
            action=action,
            effect=effect,
            role_grant_id=role_grant_id,
            policy_version=policy_version,
            reason=reason,
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
            "reason": self.reason,
        }


class PolicyDecisionBackend(Protocol):
    async def find_allowing_policy(
        self,
        *,
        tenant_id: str,
        subject: str,
        resource: str,
        action: str,
    ) -> PolicyMatch | None: ...


class ProjectedPolicyBackend:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_allowing_policy(
        self,
        *,
        tenant_id: str,
        subject: str,
        resource: str,
        action: str,
    ) -> PolicyMatch | None:
        result = await self.session.execute(
            select(ProjectedPolicy)
            .where(ProjectedPolicy.tenant_id == tenant_id)
            .where(ProjectedPolicy.subject == subject)
            .where(ProjectedPolicy.resource == resource)
            .where(ProjectedPolicy.action == action)
            .where(ProjectedPolicy.effect == "allow")
            .order_by(ProjectedPolicy.policy_version.desc())
            .limit(1)
        )
        policy = result.scalars().first()
        if policy is None:
            return None
        return PolicyMatch.from_projected_policy(policy)


class CasbinEquivalentPolicyBackend:
    """Evaluate projected policy rules using Casbin-style subject/domain/object/action fields."""

    def __init__(self, rules: list[PolicyRule] | tuple[PolicyRule, ...]) -> None:
        self.rules = tuple(rules)

    async def find_allowing_policy(
        self,
        *,
        tenant_id: str,
        subject: str,
        resource: str,
        action: str,
    ) -> PolicyMatch | None:
        matches = [
            rule
            for rule in self.rules
            if rule.tenant_id == tenant_id
            and rule.subject == subject
            and rule.resource == resource
            and rule.action == action
            and rule.effect == "allow"
        ]
        if not matches:
            return None
        rule = max(matches, key=lambda item: item.policy_version)
        return PolicyMatch.from_rule(rule, reason="matched_casbin_equivalent_policy")


class CachedPolicyDecisionBackend:
    def __init__(
        self,
        backend: PolicyDecisionBackend,
        *,
        cache: DistributedPermissionCache,
    ) -> None:
        self.backend = backend
        self.cache = cache

    async def find_allowing_policy(
        self,
        *,
        tenant_id: str,
        subject: str,
        resource: str,
        action: str,
    ) -> PolicyMatch | None:
        cached = await self.cache.get_allowing_match(
            tenant_id=tenant_id,
            subject=subject,
            resource=resource,
            action=action,
        )
        if cached is not None:
            return cached
        match = await self.backend.find_allowing_policy(
            tenant_id=tenant_id,
            subject=subject,
            resource=resource,
            action=action,
        )
        if match is not None:
            await self.cache.set_allowing_match(match)
        return match
