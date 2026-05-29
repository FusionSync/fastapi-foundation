from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from core.audit import AuditRecorder
from core.context import scheduler_background_context, use_background_context
from core.locks import LockProvider
from core.scheduler.registry import ScheduleRegistry
from core.scheduler.repository import ScheduleTriggerRepository
from core.tasks import TaskEnvelope, TaskResult
from core.tenancy import TenantStatus


class TaskSubmitter(Protocol):
    async def submit(
        self,
        envelope: TaskEnvelope,
        *,
        tenant_status: TenantStatus = "active",
    ) -> TaskResult: ...


class ScheduleTriggerProvider(Protocol):
    async def trigger(
        self,
        request: ScheduleTriggerRequest,
        *,
        tenant_status: TenantStatus = "active",
    ) -> ScheduleTriggerResult: ...


@dataclass(frozen=True, slots=True)
class ScheduleTriggerRequest:
    schedule_id: str
    tenant_id: str
    request_id: str
    trace_id: str | None = None
    planned_at: datetime | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScheduleTriggerResult:
    schedule_id: str
    task_id: str
    task_type: str
    planned_at: datetime
    triggered_at: datetime
    task_result: TaskResult
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.task_result.ok

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "schedule_id": self.schedule_id,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "planned_at": self.planned_at.isoformat(),
            "triggered_at": self.triggered_at.isoformat(),
            "task_result": self.task_result.to_dict(),
            "metadata": self.metadata,
        }


class ManualScheduleProvider:
    def __init__(
        self,
        *,
        schedule_registry: ScheduleRegistry,
        task_provider: TaskSubmitter,
        trigger_repository: ScheduleTriggerRepository | None = None,
    ) -> None:
        self.schedule_registry = schedule_registry
        self.task_provider = task_provider
        self.trigger_repository = trigger_repository

    async def trigger(
        self,
        request: ScheduleTriggerRequest,
        *,
        tenant_status: TenantStatus = "active",
    ) -> ScheduleTriggerResult:
        planned_at = _ensure_aware(request.planned_at or datetime.now(UTC))
        with use_background_context(
            scheduler_background_context(
                schedule_id=request.schedule_id,
                tenant_id=request.tenant_id,
                request_id=request.request_id,
                trace_id=request.trace_id,
            )
        ):
            registered = self.schedule_registry.get(request.schedule_id)
            triggered_at = datetime.now(UTC)
            idempotency_key = _idempotency_key(
                schedule_id=request.schedule_id,
                tenant_id=request.tenant_id,
                planned_at=planned_at,
            )
            task_id = _task_id(idempotency_key)
            task_result = await self.task_provider.submit(
                TaskEnvelope(
                    task_id=task_id,
                    task_type=registered.spec.task_type,
                    tenant_id=request.tenant_id,
                    payload=dict(request.payload),
                    idempotency_key=idempotency_key,
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                ),
                tenant_status=tenant_status,
            )
            metadata: dict[str, object] = {
                "provider": "manual",
                "trigger": registered.spec.trigger,
                "misfire_policy": registered.spec.misfire_policy,
            }
            if self.trigger_repository is not None:
                history = await self.trigger_repository.record_result(
                    schedule_id=request.schedule_id,
                    tenant_id=request.tenant_id,
                    task_id=task_id,
                    task_type=registered.spec.task_type,
                    planned_at=planned_at,
                    triggered_at=triggered_at,
                    request_id=request.request_id,
                    status=task_result.status,
                    error_message=task_result.error_message,
                    details={"task_ok": task_result.ok},
                )
                metadata["trigger_history"] = history.outcome
                metadata["trigger_log_id"] = history.log.id
            return ScheduleTriggerResult(
                schedule_id=request.schedule_id,
                task_id=task_id,
                task_type=registered.spec.task_type,
                planned_at=planned_at,
                triggered_at=triggered_at,
                task_result=task_result,
                metadata=metadata,
            )


class LockedScheduleProvider:
    def __init__(
        self,
        *,
        provider: ScheduleTriggerProvider,
        lock_provider: LockProvider,
        lock_ttl_seconds: int = 60,
    ) -> None:
        self.provider = provider
        self.lock_provider = lock_provider
        self.lock_ttl_seconds = lock_ttl_seconds

    async def trigger(
        self,
        request: ScheduleTriggerRequest,
        *,
        tenant_status: TenantStatus = "active",
    ) -> ScheduleTriggerResult:
        planned_at = _ensure_aware(request.planned_at or datetime.now(UTC))
        resolved_request = replace(request, planned_at=planned_at)
        lock_key = _lock_key(
            schedule_id=request.schedule_id,
            tenant_id=request.tenant_id,
            planned_at=planned_at,
        )
        handle = await self.lock_provider.require_acquire(
            lock_key,
            ttl_seconds=self.lock_ttl_seconds,
        )
        try:
            result = await self.provider.trigger(
                resolved_request,
                tenant_status=tenant_status,
            )
            result.metadata["lock_key"] = lock_key
            result.metadata["fencing_token"] = handle.fencing_token
            return result
        finally:
            await self.lock_provider.release(lock_key, owner_token=handle.owner_token)


class AuditedScheduleProvider:
    def __init__(
        self,
        *,
        provider: ScheduleTriggerProvider,
        audit: AuditRecorder,
    ) -> None:
        self.provider = provider
        self.audit = audit

    async def trigger(
        self,
        request: ScheduleTriggerRequest,
        *,
        tenant_status: TenantStatus = "active",
    ) -> ScheduleTriggerResult:
        try:
            result = await self.provider.trigger(request, tenant_status=tenant_status)
        except Exception as exc:
            await self.audit.record(
                action="scheduler.triggered",
                resource_type="schedule",
                resource_id=request.schedule_id,
                result="failure",
                tenant_id=request.tenant_id,
                request_id=request.request_id,
                reason=f"{type(exc).__name__}: {exc}",
                payload={
                    "schedule_id": request.schedule_id,
                    "planned_at": _planned_at_payload(request.planned_at),
                    "tenant_status": tenant_status,
                },
            )
            raise

        audit_result = "success" if result.ok else "failure"
        await self.audit.record(
            action="scheduler.triggered",
            resource_type="schedule",
            resource_id=request.schedule_id,
            result=audit_result,
            tenant_id=request.tenant_id,
            request_id=request.request_id,
            reason=result.task_result.error_message,
            payload=_audit_success_payload(result, tenant_status=tenant_status),
        )
        result.metadata["audit"] = "recorded"
        return result


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _idempotency_key(*, schedule_id: str, tenant_id: str, planned_at: datetime) -> str:
    return f"schedule:{schedule_id}:{tenant_id}:{planned_at.isoformat()}"


def _task_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    return f"schedule-{digest}"


def _lock_key(*, schedule_id: str, tenant_id: str, planned_at: datetime) -> str:
    return f"scheduler:trigger:{schedule_id}:{tenant_id}:{planned_at.isoformat()}"


def _planned_at_payload(planned_at: datetime | None) -> str | None:
    return _ensure_aware(planned_at).isoformat() if planned_at is not None else None


def _audit_success_payload(
    result: ScheduleTriggerResult,
    *,
    tenant_status: TenantStatus,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schedule_id": result.schedule_id,
        "task_id": result.task_id,
        "task_type": result.task_type,
        "task_status": result.task_result.status,
        "planned_at": result.planned_at.isoformat(),
        "triggered_at": result.triggered_at.isoformat(),
        "tenant_status": tenant_status,
    }
    if "lock_key" in result.metadata:
        payload["lock_key"] = result.metadata["lock_key"]
    if "fencing_token" in result.metadata:
        payload["fencing_token"] = result.metadata["fencing_token"]
    if "scheduler_provider" in result.metadata:
        payload["scheduler_provider"] = result.metadata["scheduler_provider"]
    return payload
