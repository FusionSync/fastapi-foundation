from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import EventEnvelope
from core.exceptions import AppError
from core.permissions.cache import PermissionCache
from core.permissions.models import ProjectedPolicy, RoleGrant, RoleTemplate

ROLE_GRANT_CHANGED_EVENT = "permissions.role_grant_changed"


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


class PolicyProjector:
    def __init__(
        self,
        session: AsyncSession,
        *,
        cache: PermissionCache | None = None,
    ) -> None:
        self.session = session
        self.cache = cache or PermissionCache()

    async def handle_role_grant_changed(self, envelope: EventEnvelope) -> None:
        grant_id = envelope.payload.get("grant_id")
        if not isinstance(grant_id, str) or not grant_id:
            raise AppError(
                "VALIDATION_ERROR",
                "role grant event requires grant_id",
                status_code=400,
            )
        grant = await self.session.get(RoleGrant, grant_id)
        if grant is None:
            raise AppError("NOT_FOUND", f"RoleGrant {grant_id!r} not found", status_code=404)
        role_template = await self.session.get(RoleTemplate, grant.role_template_id)
        if role_template is None:
            raise AppError(
                "NOT_FOUND",
                f"RoleTemplate {grant.role_template_id!r} not found",
                status_code=404,
            )
        await self.project_grant(grant, role_template)

    async def project_grant(
        self,
        grant: RoleGrant,
        role_template: RoleTemplate,
    ) -> list[PolicyRule]:
        rules = _rules_for_grant(grant, role_template)
        await self.session.execute(
            delete(ProjectedPolicy).where(ProjectedPolicy.role_grant_id == grant.id)
        )
        for rule in rules:
            self.session.add(
                ProjectedPolicy(
                    tenant_id=rule.tenant_id,
                    subject=rule.subject,
                    resource=rule.resource,
                    action=rule.action,
                    effect=rule.effect,
                    role_grant_id=rule.role_grant_id,
                    policy_version=rule.policy_version,
                )
            )
        self.cache.invalidate()
        await self.session.flush()
        return rules

    async def reconcile(self, *, repair: bool = False) -> ReconciliationResult:
        grants = list((await self.session.execute(select(RoleGrant))).scalars().all())
        templates = {
            template.id: template
            for template in (await self.session.execute(select(RoleTemplate))).scalars().all()
        }
        expected = [
            rule
            for grant in grants
            if grant.role_template_id in templates
            for rule in _rules_for_grant(grant, templates[grant.role_template_id])
        ]
        actual = [
            _rule_from_policy(policy)
            for policy in (await self.session.execute(select(ProjectedPolicy))).scalars().all()
        ]
        expected_by_key = {rule.key: rule for rule in expected}
        actual_by_key = {rule.key: rule for rule in actual}
        missing = [rule for key, rule in expected_by_key.items() if key not in actual_by_key]
        stale = [rule for key, rule in actual_by_key.items() if key not in expected_by_key]

        if repair and (missing or stale):
            await self.session.execute(delete(ProjectedPolicy))
            for grant in grants:
                role_template = templates.get(grant.role_template_id)
                if role_template is not None:
                    await self.project_grant(grant, role_template)
            return ReconciliationResult(repaired=True)
        return ReconciliationResult(repaired=False, missing=missing, stale=stale)


def _rules_for_grant(grant: RoleGrant, role_template: RoleTemplate) -> list[PolicyRule]:
    subject = f"{grant.subject_type}:{grant.subject_id}"
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


def _rule_from_policy(policy: ProjectedPolicy) -> PolicyRule:
    return PolicyRule(
        tenant_id=policy.tenant_id,
        subject=policy.subject,
        resource=policy.resource,
        action=policy.action,
        effect=policy.effect,
        role_grant_id=policy.role_grant_id,
        policy_version=policy.policy_version,
    )
