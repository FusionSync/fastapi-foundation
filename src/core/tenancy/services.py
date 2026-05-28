from __future__ import annotations

import inspect

from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditRecorder
from core.outbox import OutboxRepository
from core.tenancy.events import (
    TENANT_ARCHIVED_EVENT,
    TENANT_CREATED_EVENT,
    TENANT_DELETED_EVENT,
    TENANT_DELETING_EVENT,
    TENANT_REACTIVATED_EVENT,
    TENANT_SUSPENDED_EVENT,
    publish_tenant_lifecycle_event,
)
from core.tenancy.lifecycle import SessionRevocationHook, TenantStatus, validate_tenant_transition
from core.tenancy.models import Tenant, TenantMember


class TenantLifecycleService:
    def __init__(
        self,
        session: AsyncSession,
        outbox: OutboxRepository,
        *,
        session_revocation_hook: SessionRevocationHook | None = None,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.outbox = outbox
        self.session_revocation_hook = session_revocation_hook
        self.audit = audit

    async def provision_tenant(
        self,
        *,
        tenant_id: str,
        name: str,
        code: str,
        owner_user_id: str,
        actor_id: str,
        request_id: str,
        deployment_mode: str = "local",
    ) -> Tenant:
        tenant = Tenant(
            id=tenant_id,
            name=name,
            code=code,
            status="provisioning",
            deployment_mode=deployment_mode,
        )
        self.session.add(tenant)
        self.session.add(
            TenantMember(
                tenant_id=tenant_id,
                user_id=owner_user_id,
                status="active",
            )
        )
        validate_tenant_transition("provisioning", "active")
        tenant.status = "active"
        await publish_tenant_lifecycle_event(
            self.outbox,
            TENANT_CREATED_EVENT,
            tenant=tenant,
            actor_id=actor_id,
            request_id=request_id,
            extra={"owner_user_id": owner_user_id},
        )
        if self.audit is not None:
            await self.audit.record(
                action=TENANT_CREATED_EVENT,
                resource_type="tenant",
                resource_id=tenant.id,
                result="success",
                tenant_id=tenant.id,
                actor_id=actor_id,
                request_id=request_id,
                payload={
                    "from_status": "provisioning",
                    "to_status": "active",
                    "event_type": TENANT_CREATED_EVENT,
                    "revoke_sessions": False,
                    "owner_user_id": owner_user_id,
                },
            )
        return tenant

    async def suspend_tenant(
        self,
        tenant: Tenant,
        *,
        actor_id: str,
        request_id: str,
        reason: str,
    ) -> Tenant:
        await self._transition(
            tenant,
            target="suspended",
            event_type=TENANT_SUSPENDED_EVENT,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            revoke_sessions=True,
        )
        return tenant

    async def reactivate_tenant(
        self,
        tenant: Tenant,
        *,
        actor_id: str,
        request_id: str,
        reason: str,
    ) -> Tenant:
        await self._transition(
            tenant,
            target="active",
            event_type=TENANT_REACTIVATED_EVENT,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            revoke_sessions=False,
        )
        return tenant

    async def begin_delete_tenant(
        self,
        tenant: Tenant,
        *,
        actor_id: str,
        request_id: str,
        reason: str,
    ) -> Tenant:
        await self._transition(
            tenant,
            target="deleting",
            event_type=TENANT_DELETING_EVENT,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            revoke_sessions=True,
        )
        return tenant

    async def finish_delete_tenant(
        self,
        tenant: Tenant,
        *,
        target: TenantStatus,
        actor_id: str,
        request_id: str,
        reason: str,
    ) -> Tenant:
        if target not in {"archived", "deleted"}:
            raise ValueError("finish_delete_tenant target must be archived or deleted")
        event_type = TENANT_ARCHIVED_EVENT if target == "archived" else TENANT_DELETED_EVENT
        await self._transition(
            tenant,
            target=target,
            event_type=event_type,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            revoke_sessions=False,
        )
        return tenant

    async def _transition(
        self,
        tenant: Tenant,
        *,
        target: TenantStatus,
        event_type: str,
        actor_id: str,
        request_id: str,
        reason: str,
        revoke_sessions: bool,
    ) -> None:
        from_status = _status(tenant)
        validate_tenant_transition(from_status, target)
        tenant.status = target
        if revoke_sessions:
            await self._revoke_sessions(tenant.id, reason)
        await publish_tenant_lifecycle_event(
            self.outbox,
            event_type,
            tenant=tenant,
            actor_id=actor_id,
            request_id=request_id,
            extra={"reason": reason},
        )
        if self.audit is not None:
            await self.audit.record(
                action=event_type,
                resource_type="tenant",
                resource_id=tenant.id,
                result="success",
                tenant_id=tenant.id,
                actor_id=actor_id,
                reason=reason,
                request_id=request_id,
                payload={
                    "from_status": from_status,
                    "to_status": target,
                    "event_type": event_type,
                    "revoke_sessions": revoke_sessions,
                },
            )

    async def _revoke_sessions(self, tenant_id: str, reason: str) -> None:
        if self.session_revocation_hook is None:
            return
        result = self.session_revocation_hook(tenant_id, reason)
        if inspect.isawaitable(result):
            await result


def _status(tenant: Tenant) -> TenantStatus:
    return tenant.status  # type: ignore[return-value]
