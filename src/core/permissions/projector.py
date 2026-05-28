from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import EventEnvelope
from core.exceptions import AppError
from core.permissions.cache import PermissionCache
from core.permissions.models import ProjectedPolicy, RoleGrant, RoleTemplate
from core.permissions.policies import (
    PolicyRule,
    ReconciliationResult,
    projected_policy_from_rule,
    rule_from_policy,
    rules_for_grant,
)

ROLE_GRANT_CHANGED_EVENT = "permissions.role_grant_changed"


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
        rules = rules_for_grant(grant, role_template)
        await self.session.execute(
            delete(ProjectedPolicy).where(ProjectedPolicy.role_grant_id == grant.id)
        )
        for rule in rules:
            self.session.add(projected_policy_from_rule(rule))
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
            for rule in rules_for_grant(grant, templates[grant.role_template_id])
        ]
        actual = [
            rule_from_policy(policy)
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
