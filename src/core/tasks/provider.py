from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Literal

from core.tasks.registry import TaskEnvelope, TaskRegistry
from core.tasks.repository import TaskRunRepository
from core.tenancy import TenantStatus, assert_tenant_operation_allowed

TaskStatus = Literal["succeeded", "failed"]


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_id: str
    task_type: str
    status: TaskStatus
    result_payload: dict[str, Any] | None = None
    error_message: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status,
            "result_payload": self.result_payload,
            "error_message": self.error_message,
            "metadata": self.metadata,
        }


class SyncTaskProvider:
    def __init__(
        self,
        task_registry: TaskRegistry,
        *,
        task_repository: TaskRunRepository | None = None,
    ) -> None:
        self.task_registry = task_registry
        self.task_repository = task_repository

    async def submit(
        self,
        envelope: TaskEnvelope,
        *,
        tenant_status: TenantStatus = "active",
    ) -> TaskResult:
        assert_tenant_operation_allowed(
            tenant_id=envelope.tenant_id,
            status=tenant_status,
            operation="task",
        )
        registered = self.task_registry.get(envelope.task_type)
        task_run = None
        if self.task_repository is not None:
            task_run = await self.task_repository.start(
                envelope,
                queue=registered.spec.queue,
            )
        try:
            result = registered.handler(envelope)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            if task_run is not None and self.task_repository is not None:
                await self.task_repository.mark_failed(
                    task_run,
                    error_message=error_message,
                )
            return TaskResult(
                task_id=envelope.task_id,
                task_type=envelope.task_type,
                status="failed",
                error_message=error_message,
                metadata={"provider": "sync", "queue": registered.spec.queue},
            )
        if task_run is not None and self.task_repository is not None:
            await self.task_repository.mark_succeeded(
                task_run,
                result_payload=result,
            )
        return TaskResult(
            task_id=envelope.task_id,
            task_type=envelope.task_type,
            status="succeeded",
            result_payload=result,
            metadata={"provider": "sync", "queue": registered.spec.queue},
        )
