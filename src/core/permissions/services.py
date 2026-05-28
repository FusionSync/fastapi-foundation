from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditRecorder
from core.outbox import OutboxRepository
from core.permissions.models import RoleGrant
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
        reason: str | None = None,
        policy_version: int = 1,
    ) -> RoleGrant:
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
