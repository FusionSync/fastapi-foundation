from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from core.tasks.models import TaskRun
from core.tasks.registry import TaskEnvelope, TaskRegistry
from core.tasks.repository import TaskRunRepository
from core.tenancy import TenantStatus, assert_tenant_operation_allowed

TaskStatus = Literal["succeeded", "failed", "dead_letter"]
TaskHandler = Callable[[TaskEnvelope], Awaitable[dict[str, Any] | None] | dict[str, Any] | None]


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
        max_attempts: int = 3,
    ) -> None:
        self.task_registry = task_registry
        self.task_repository = task_repository
        self.max_attempts = max_attempts

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
                max_attempts=self.max_attempts,
            )
        return await self._execute(
            envelope,
            handler=registered.handler,
            queue=registered.spec.queue,
            task_run=task_run,
        )

    async def retry(
        self,
        task_run: TaskRun,
        *,
        tenant_status: TenantStatus = "active",
    ) -> TaskResult:
        if self.task_repository is None:
            raise RuntimeError("task_repository is required to retry a persisted task run")
        assert_tenant_operation_allowed(
            tenant_id=task_run.tenant_id,
            status=tenant_status,
            operation="task",
        )
        registered = self.task_registry.get(task_run.task_type)
        await self.task_repository.start_retry(task_run)
        envelope = self.task_repository.to_envelope(task_run)
        return await self._execute(
            envelope,
            handler=registered.handler,
            queue=registered.spec.queue,
            task_run=task_run,
        )

    async def _execute(
        self,
        envelope: TaskEnvelope,
        *,
        handler: TaskHandler,
        queue: str,
        task_run: TaskRun | None,
    ) -> TaskResult:
        try:
            result = handler(envelope)
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
                status=task_run.status if task_run is not None else "failed",
                error_message=error_message,
                metadata={"provider": "sync", "queue": queue},
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
            metadata={"provider": "sync", "queue": queue},
        )
