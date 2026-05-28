from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditRecorder
from core.exceptions import AppError
from core.outbox import OutboxRepository
from core.permissions.decisions import PLATFORM_TENANT_ID, AuthorizationDecision
from core.permissions.models import ProjectedPolicy, RoleGrant
from core.permissions.projector import ROLE_GRANT_CHANGED_EVENT


class RoleGrantService:
    def __init__(
        self,
        session: AsyncSession,
        outbox: OutboxRepository,
        *,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.outbox = outbox
        self.audit = audit

    async def grant_role(
        self,
        *,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
        role_template_id: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None = None,
        reason: str | None = None,
        policy_version: int = 1,
    ) -> RoleGrant:
        _assert_role_grant_mutation_authorized(
            authorization_decision=authorization_decision,
            tenant_id=tenant_id,
            actor_id=actor_id,
            mutation="grant",
        )
        grant = RoleGrant(
            id=str(uuid4()),
            tenant_id=tenant_id,
            subject_type=subject_type,
            subject_id=subject_id,
            role_template_id=role_template_id,
            policy_version=policy_version,
        )
        self.session.add(grant)
        await self.outbox.add(
            event_type=ROLE_GRANT_CHANGED_EVENT,
            aggregate_type="role_grant",
            aggregate_id=grant.id,
            tenant_id=tenant_id,
            payload={
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "request_id": request_id,
                "grant_id": grant.id,
            },
        )
        if self.audit is not None:
            await self.audit.record(
                action="role.granted",
                resource_type="role_grant",
                resource_id=grant.id,
                result="success",
                tenant_id=tenant_id,
                actor_id=actor_id,
                reason=reason,
                policy_version=policy_version,
                request_id=request_id,
                payload={
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "role_template_id": role_template_id,
                },
        )
        return grant

    async def revoke_role(
        self,
        *,
        grant_id: str,
        actor_id: str,
        request_id: str,
        authorization_decision: AuthorizationDecision | None = None,
        reason: str | None = None,
    ) -> RoleGrant:
        grant = await self.session.get(RoleGrant, grant_id)
        if grant is None:
            raise AppError("NOT_FOUND", f"RoleGrant {grant_id!r} not found", status_code=404)
        _assert_role_grant_mutation_authorized(
            authorization_decision=authorization_decision,
            tenant_id=grant.tenant_id,
            actor_id=actor_id,
            mutation="revoke",
        )
        await self.outbox.add(
            event_type=ROLE_GRANT_CHANGED_EVENT,
            aggregate_type="role_grant",
            aggregate_id=grant.id,
            tenant_id=grant.tenant_id,
            payload={
                "tenant_id": grant.tenant_id,
                "actor_id": actor_id,
                "request_id": request_id,
                "grant_id": grant.id,
                "change_type": "revoked",
            },
        )
        if self.audit is not None:
            await self.audit.record(
                action="role.revoked",
                resource_type="role_grant",
                resource_id=grant.id,
                result="success",
                tenant_id=grant.tenant_id,
                actor_id=actor_id,
                reason=reason,
                policy_version=grant.policy_version,
                request_id=request_id,
                payload={
                    "subject_type": grant.subject_type,
                    "subject_id": grant.subject_id,
                    "role_template_id": grant.role_template_id,
                },
            )
        await self.session.execute(
            delete(ProjectedPolicy).where(ProjectedPolicy.role_grant_id == grant.id)
        )
        await self.session.delete(grant)
        await self.session.flush()
        return grant


def _assert_role_grant_mutation_authorized(
    *,
    authorization_decision: AuthorizationDecision | None,
    tenant_id: str,
    actor_id: str,
    mutation: str,
) -> None:
    if authorization_decision is None:
        _raise_role_grant_denied(
            "Role grant mutation requires an authorization decision",
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
    if not authorization_decision.allowed:
        _raise_role_grant_denied(
            "Role grant mutation requires an allowed authorization decision",
            tenant_id=tenant_id,
            actor_id=actor_id,
            reason=authorization_decision.reason,
        )
    if authorization_decision.user_id != actor_id:
        _raise_role_grant_denied(
            "Role grant actor must match authorization decision user",
            tenant_id=tenant_id,
            actor_id=actor_id,
            reason=authorization_decision.reason,
        )
    if authorization_decision.tenant_id not in {tenant_id, PLATFORM_TENANT_ID}:
        _raise_role_grant_denied(
            "Role grant mutation decision tenant does not match target tenant",
            tenant_id=tenant_id,
            actor_id=actor_id,
            reason=authorization_decision.reason,
        )
    if authorization_decision.resource != "role_grant":
        _raise_role_grant_denied(
            "Role grant mutation requires role_grant permission",
            tenant_id=tenant_id,
            actor_id=actor_id,
            reason=authorization_decision.reason,
        )
    if authorization_decision.action not in {"manage", mutation}:
        _raise_role_grant_denied(
            "Role grant mutation decision action is not sufficient",
            tenant_id=tenant_id,
            actor_id=actor_id,
            reason=authorization_decision.reason,
        )


def _raise_role_grant_denied(
    message: str,
    *,
    tenant_id: str,
    actor_id: str,
    reason: str | None = None,
) -> None:
    raise AppError(
        "PERMISSION_DENIED",
        message,
        status_code=403,
        details={
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "reason": reason,
        },
    )
