from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from core.scheduler.registry import ScheduleRegistry
from core.tasks import TaskEnvelope, TaskResult
from core.tenancy import TenantStatus


class TaskSubmitter(Protocol):
    async def submit(
        self,
        envelope: TaskEnvelope,
        *,
        tenant_status: TenantStatus = "active",
    ) -> TaskResult: ...


@dataclass(frozen=True, slots=True)
class ScheduleTriggerRequest:
    schedule_id: str
    tenant_id: str
    request_id: str
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
    ) -> None:
        self.schedule_registry = schedule_registry
        self.task_provider = task_provider

    async def trigger(
        self,
        request: ScheduleTriggerRequest,
        *,
        tenant_status: TenantStatus = "active",
    ) -> ScheduleTriggerResult:
        registered = self.schedule_registry.get(request.schedule_id)
        planned_at = _ensure_aware(request.planned_at or datetime.now(UTC))
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
            ),
            tenant_status=tenant_status,
        )
        return ScheduleTriggerResult(
            schedule_id=request.schedule_id,
            task_id=task_id,
            task_type=registered.spec.task_type,
            planned_at=planned_at,
            triggered_at=triggered_at,
            task_result=task_result,
            metadata={
                "provider": "manual",
                "trigger": registered.spec.trigger,
                "misfire_policy": registered.spec.misfire_policy,
            },
        )


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _idempotency_key(*, schedule_id: str, tenant_id: str, planned_at: datetime) -> str:
    return f"schedule:{schedule_id}:{tenant_id}:{planned_at.isoformat()}"


def _task_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
    return f"schedule-{digest}"
