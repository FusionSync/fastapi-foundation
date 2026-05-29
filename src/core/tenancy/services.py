from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import AuditRecorder
from core.events import EventPublisher
from core.exceptions import AppError
from core.permissions import (
    PLATFORM_TENANT_ID,
    AuthorizationDecision,
    assert_authorization_decision,
)
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
from core.tenancy.models import Tenant, TenantLifecycleStepRecord, TenantMember

TenantDeletionTarget = Literal["archived", "deleted"]
TenantDeletionStep = Literal[
    "mark_deleting",
    "cancel_tasks",
    "cleanup_business_data",
    "cleanup_files",
    "finish",
]
TenantCleanupHook = Callable[
    [str, str],
    Awaitable[Mapping[str, Any] | int | None] | Mapping[str, Any] | int | None,
]


class TenantTaskCancellationRepository(Protocol):
    async def cancel_for_tenant(
        self,
        *,
        tenant_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> Sequence[object]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class TenantDeletionResult:
    tenant_id: str
    status: TenantStatus
    steps: tuple[TenantLifecycleStepRecord, ...]


_DELETE_WORKFLOW = "delete"
_DELETE_STEPS: tuple[TenantDeletionStep, ...] = (
    "mark_deleting",
    "cancel_tasks",
    "cleanup_business_data",
    "cleanup_files",
    "finish",
)
_DELETE_STEP_ORDER = {step: index for index, step in enumerate(_DELETE_STEPS, start=1)}


class TenantLifecycleService:
    def __init__(
        self,
        session: AsyncSession,
        events: EventPublisher,
        *,
        session_revocation_hook: SessionRevocationHook | None = None,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.events = events
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
        authorization_decision: AuthorizationDecision | None = None,
    ) -> Tenant:
        _assert_tenant_mutation_authorized(
            authorization_decision=authorization_decision,
            actor_id=actor_id,
            mutation="provision",
        )
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
            self.events,
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
        authorization_decision: AuthorizationDecision | None = None,
    ) -> Tenant:
        await self._transition(
            tenant,
            target="suspended",
            event_type=TENANT_SUSPENDED_EVENT,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            revoke_sessions=True,
            mutation="suspend",
            authorization_decision=authorization_decision,
        )
        return tenant

    async def reactivate_tenant(
        self,
        tenant: Tenant,
        *,
        actor_id: str,
        request_id: str,
        reason: str,
        authorization_decision: AuthorizationDecision | None = None,
    ) -> Tenant:
        await self._transition(
            tenant,
            target="active",
            event_type=TENANT_REACTIVATED_EVENT,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            revoke_sessions=False,
            mutation="reactivate",
            authorization_decision=authorization_decision,
        )
        return tenant

    async def begin_delete_tenant(
        self,
        tenant: Tenant,
        *,
        actor_id: str,
        request_id: str,
        reason: str,
        authorization_decision: AuthorizationDecision | None = None,
    ) -> Tenant:
        await self._transition(
            tenant,
            target="deleting",
            event_type=TENANT_DELETING_EVENT,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            revoke_sessions=True,
            mutation="delete",
            authorization_decision=authorization_decision,
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
        authorization_decision: AuthorizationDecision | None = None,
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
            mutation="delete",
            authorization_decision=authorization_decision,
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
        mutation: str,
        authorization_decision: AuthorizationDecision | None,
    ) -> None:
        _assert_tenant_mutation_authorized(
            authorization_decision=authorization_decision,
            actor_id=actor_id,
            mutation=mutation,
        )
        from_status = _status(tenant)
        validate_tenant_transition(from_status, target)
        tenant.status = target
        if revoke_sessions:
            await self._revoke_sessions(tenant.id, reason)
        await publish_tenant_lifecycle_event(
            self.events,
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


class TenantDeletionOrchestrator:
    def __init__(
        self,
        session: AsyncSession,
        events: EventPublisher,
        *,
        task_repository: TenantTaskCancellationRepository | None = None,
        session_revocation_hook: SessionRevocationHook | None = None,
        business_cleanup_hook: TenantCleanupHook | None = None,
        file_cleanup_hook: TenantCleanupHook | None = None,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.events = events
        self.task_repository = task_repository
        self.session_revocation_hook = session_revocation_hook
        self.business_cleanup_hook = business_cleanup_hook
        self.file_cleanup_hook = file_cleanup_hook
        self.audit = audit

    async def run(
        self,
        tenant: Tenant,
        *,
        target: TenantDeletionTarget,
        actor_id: str,
        request_id: str,
        reason: str,
        authorization_decision: AuthorizationDecision | None = None,
    ) -> TenantDeletionResult:
        if target not in {"archived", "deleted"}:
            raise ValueError("tenant deletion target must be archived or deleted")
        _assert_tenant_mutation_authorized(
            authorization_decision=authorization_decision,
            actor_id=actor_id,
            mutation="delete",
        )
        completed: list[TenantLifecycleStepRecord] = []
        for step in _DELETE_STEPS:
            record = await self._step_record(tenant.id, step)
            if record.status == "succeeded":
                completed.append(record)
                continue
            try:
                result_payload = await self._execute_step(
                    step,
                    tenant,
                    target=target,
                    actor_id=actor_id,
                    request_id=request_id,
                    reason=reason,
                    authorization_decision=authorization_decision,
                )
            except Exception as exc:
                await self._mark_step_failed(
                    record,
                    error=exc,
                    actor_id=actor_id,
                    request_id=request_id,
                    reason=reason,
                )
                raise AppError(
                    "TENANT_DELETE_STEP_FAILED",
                    "Tenant deletion step failed",
                    status_code=409,
                    details={
                        "tenant_id": tenant.id,
                        "step": step,
                        "attempt_count": record.attempt_count,
                        "forward_fix_required": True,
                    },
                ) from exc
            await self._mark_step_succeeded(
                record,
                result_payload=result_payload,
                actor_id=actor_id,
                request_id=request_id,
                reason=reason,
            )
            completed.append(record)
        return TenantDeletionResult(
            tenant_id=tenant.id,
            status=_status(tenant),
            steps=tuple(completed),
        )

    async def _execute_step(
        self,
        step: TenantDeletionStep,
        tenant: Tenant,
        *,
        target: TenantDeletionTarget,
        actor_id: str,
        request_id: str,
        reason: str,
        authorization_decision: AuthorizationDecision | None,
    ) -> dict[str, Any]:
        if step == "mark_deleting":
            return await self._mark_deleting(
                tenant,
                actor_id=actor_id,
                request_id=request_id,
                reason=reason,
                authorization_decision=authorization_decision,
            )
        if step == "cancel_tasks":
            return await self._cancel_tasks(tenant.id, reason=reason)
        if step == "cleanup_business_data":
            return await self._invoke_cleanup_hook(self.business_cleanup_hook, tenant.id, reason)
        if step == "cleanup_files":
            return await self._invoke_cleanup_hook(self.file_cleanup_hook, tenant.id, reason)
        return await self._finish(
            tenant,
            target=target,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            authorization_decision=authorization_decision,
        )

    async def _mark_deleting(
        self,
        tenant: Tenant,
        *,
        actor_id: str,
        request_id: str,
        reason: str,
        authorization_decision: AuthorizationDecision | None,
    ) -> dict[str, Any]:
        if tenant.status == "deleting":
            return {"status": "already_deleting"}
        previous_status = tenant.status
        await TenantLifecycleService(
            self.session,
            self.events,
            session_revocation_hook=self.session_revocation_hook,
            audit=self.audit,
        ).begin_delete_tenant(
            tenant,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            authorization_decision=authorization_decision,
        )
        return {"from_status": previous_status, "to_status": "deleting"}

    async def _cancel_tasks(self, tenant_id: str, *, reason: str) -> dict[str, int]:
        if self.task_repository is None:
            return {"cancelled_task_count": 0}
        cancelled = await self.task_repository.cancel_for_tenant(
            tenant_id=tenant_id,
            reason=reason,
        )
        return {"cancelled_task_count": len(cancelled)}

    async def _invoke_cleanup_hook(
        self,
        hook: TenantCleanupHook | None,
        tenant_id: str,
        reason: str,
    ) -> dict[str, Any]:
        if hook is None:
            return {}
        result = hook(tenant_id, reason)
        if inspect.isawaitable(result):
            result = await result
        return _cleanup_result_payload(result)

    async def _finish(
        self,
        tenant: Tenant,
        *,
        target: TenantDeletionTarget,
        actor_id: str,
        request_id: str,
        reason: str,
        authorization_decision: AuthorizationDecision | None,
    ) -> dict[str, str]:
        if tenant.status == target:
            return {"status": f"already_{target}"}
        previous_status = tenant.status
        await TenantLifecycleService(
            self.session,
            self.events,
            audit=self.audit,
        ).finish_delete_tenant(
            tenant,
            target=target,
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
            authorization_decision=authorization_decision,
        )
        return {"from_status": previous_status, "to_status": target}

    async def _step_record(
        self,
        tenant_id: str,
        step: TenantDeletionStep,
    ) -> TenantLifecycleStepRecord:
        result = await self.session.execute(
            select(TenantLifecycleStepRecord)
            .where(TenantLifecycleStepRecord.tenant_id == tenant_id)
            .where(TenantLifecycleStepRecord.workflow == _DELETE_WORKFLOW)
            .where(TenantLifecycleStepRecord.step == step)
        )
        record = result.scalars().first()
        if record is not None:
            return record
        record = TenantLifecycleStepRecord(
            id=_step_record_id(tenant_id, step),
            tenant_id=tenant_id,
            workflow=_DELETE_WORKFLOW,
            step=step,
            status="pending",
            attempt_count=0,
            forward_fix_required=False,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def _mark_step_succeeded(
        self,
        record: TenantLifecycleStepRecord,
        *,
        result_payload: dict[str, Any],
        actor_id: str,
        request_id: str,
        reason: str,
    ) -> None:
        resolved_now = datetime.now(UTC)
        record.status = "succeeded"
        record.attempt_count += 1
        record.forward_fix_required = False
        record.result_payload = result_payload
        record.last_error = None
        record.started_at = resolved_now
        record.finished_at = resolved_now
        await self.session.flush()
        await self._audit_step(
            record,
            result="success",
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
        )

    async def _mark_step_failed(
        self,
        record: TenantLifecycleStepRecord,
        *,
        error: Exception,
        actor_id: str,
        request_id: str,
        reason: str,
    ) -> None:
        resolved_now = datetime.now(UTC)
        record.status = "failed"
        record.attempt_count += 1
        record.forward_fix_required = True
        record.result_payload = None
        record.last_error = f"{type(error).__name__}: {error}"
        record.started_at = resolved_now
        record.finished_at = resolved_now
        await self.session.flush()
        await self._audit_step(
            record,
            result="failure",
            actor_id=actor_id,
            request_id=request_id,
            reason=reason,
        )

    async def _audit_step(
        self,
        record: TenantLifecycleStepRecord,
        *,
        result: Literal["success", "failure"],
        actor_id: str,
        request_id: str,
        reason: str,
    ) -> None:
        if self.audit is None:
            return
        await self.audit.record(
            action=f"tenant.deletion_step.{record.status}",
            resource_type="tenant_lifecycle_step",
            resource_id=record.id,
            result=result,
            tenant_id=record.tenant_id,
            actor_id=actor_id,
            reason=reason,
            request_id=request_id,
            payload={
                "workflow": record.workflow,
                "step": record.step,
                "attempt_count": record.attempt_count,
                "forward_fix_required": record.forward_fix_required,
                "last_error": record.last_error,
            },
        )


def _status(tenant: Tenant) -> TenantStatus:
    return tenant.status  # type: ignore[return-value]


def _step_record_id(tenant_id: str, step: TenantDeletionStep) -> str:
    return f"{tenant_id}:delete:{_DELETE_STEP_ORDER[step]:02d}_{step}"


def _cleanup_result_payload(result: Mapping[str, Any] | int | None) -> dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, int):
        return {"count": result}
    return dict(result)


def _assert_tenant_mutation_authorized(
    *,
    authorization_decision: AuthorizationDecision | None,
    actor_id: str,
    mutation: str,
) -> None:
    assert_authorization_decision(
        authorization_decision,
        tenant_id=PLATFORM_TENANT_ID,
        actor_id=actor_id,
        resource="tenant",
        actions={"manage", mutation},
        operation="Tenant lifecycle mutation",
        allow_platform=False,
    )
