from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.permissions.cache import (
    PermissionCache,
    PermissionCacheInvalidator,
    invalidate_permission_cache,
)
from core.permissions.models import ProjectedPolicy, RoleGrant, RoleTemplate
from core.permissions.policies import (
    PolicyRule,
    ReconciliationResult,
    projected_policy_from_rule,
    rule_from_policy,
    rules_for_grant,
)
from core.permissions.registry import PermissionRegistry

ROLE_GRANT_CHANGED_EVENT = "permissions.role_grant_changed"

if TYPE_CHECKING:
    from core.events import EventEnvelope


class PolicyProjector:
    def __init__(
        self,
        session: AsyncSession,
        *,
        cache: PermissionCacheInvalidator | None = None,
        permission_registry: PermissionRegistry | None = None,
    ) -> None:
        self.session = session
        self.cache = cache or PermissionCache()
        self.permission_registry = permission_registry

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
            subject_type = envelope.payload.get("subject_type")
            subject_id = envelope.payload.get("subject_id")
            subject = (
                f"{subject_type}:{subject_id}"
                if isinstance(subject_type, str)
                and subject_type.strip()
                and isinstance(subject_id, str)
                and subject_id.strip()
                else None
            )
            await self.remove_grant_projection(
                grant_id,
                tenant_id=envelope.tenant_id,
                subject=subject,
            )
            return
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
        rules = rules_for_grant(
            grant,
            role_template,
            permission_registry=self.permission_registry,
        )
        await self.session.execute(
            delete(ProjectedPolicy).where(ProjectedPolicy.role_grant_id == grant.id)
        )
        for rule in rules:
            self.session.add(projected_policy_from_rule(rule))
        await invalidate_permission_cache(
            self.cache,
            tenant_id=grant.tenant_id,
            subject=_subject_for_grant(grant),
        )
        await self.session.flush()
        return rules

    async def remove_grant_projection(
        self,
        grant_id: str,
        *,
        tenant_id: str | None = None,
        subject: str | None = None,
    ) -> None:
        projected_subjects = {
            (policy_tenant_id, policy_subject)
            for policy_tenant_id, policy_subject in (
                await self.session.execute(
                    select(ProjectedPolicy.tenant_id, ProjectedPolicy.subject).where(
                        ProjectedPolicy.role_grant_id == grant_id
                    )
                )
            ).all()
        }
        if tenant_id is not None and subject is not None:
            projected_subjects.add((tenant_id, subject))
        await self.session.execute(
            delete(ProjectedPolicy).where(ProjectedPolicy.role_grant_id == grant_id)
        )
        await self._invalidate_subjects(projected_subjects)
        await self.session.flush()

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
            for rule in rules_for_grant(
                grant,
                templates[grant.role_template_id],
                permission_registry=self.permission_registry,
            )
        ]
        policies = list((await self.session.execute(select(ProjectedPolicy))).scalars().all())
        expected_by_key = {rule.key: rule for rule in expected}
        actual_by_key = {rule_from_policy(policy).key: policy for policy in policies}
        missing: list[PolicyRule] = []
        stale: list[PolicyRule] = []
        stale_policy_ids: list[str] = []

        for key, expected_rule in expected_by_key.items():
            actual_policy = actual_by_key.get(key)
            if actual_policy is None:
                missing.append(expected_rule)
                continue
            actual_rule = rule_from_policy(actual_policy)
            if actual_rule != expected_rule:
                missing.append(expected_rule)
                stale.append(actual_rule)
                stale_policy_ids.append(actual_policy.id)

        for key, actual_policy in actual_by_key.items():
            if key in expected_by_key:
                continue
            stale.append(rule_from_policy(actual_policy))
            stale_policy_ids.append(actual_policy.id)

        if repair and (missing or stale):
            if stale_policy_ids:
                await self.session.execute(
                    delete(ProjectedPolicy).where(ProjectedPolicy.id.in_(stale_policy_ids))
                )
            for rule in missing:
                self.session.add(projected_policy_from_rule(rule))
            await self._invalidate_subjects(
                {(rule.tenant_id, rule.subject) for rule in (*missing, *stale)}
            )
            await self.session.flush()
            return ReconciliationResult(repaired=True)
        return ReconciliationResult(repaired=False, missing=missing, stale=stale)

    async def _invalidate_subjects(self, subjects: set[tuple[str, str]]) -> None:
        if not subjects:
            await invalidate_permission_cache(self.cache)
            return
        for tenant_id, subject in sorted(subjects):
            await invalidate_permission_cache(self.cache, tenant_id=tenant_id, subject=subject)


def _subject_for_grant(grant: RoleGrant) -> str:
    return f"{grant.subject_type}:{grant.subject_id}"
