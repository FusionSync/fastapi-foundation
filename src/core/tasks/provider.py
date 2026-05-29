from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from core.context import task_background_context, use_background_context
from core.tasks.models import TaskRun
from core.tasks.registry import TaskEnvelope, TaskRegistry
from core.tasks.repository import TaskRunRepository
from core.tenancy import TenantStatus, assert_tenant_operation_allowed

TaskStatus = Literal["pending", "running", "succeeded", "failed", "dead_letter", "cancelled"]
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
        return self.status in {"pending", "running", "succeeded"} and self.error_message is None

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
            start_result = await self.task_repository.start_once(
                envelope,
                queue=registered.spec.queue,
                max_attempts=self.max_attempts,
            )
            task_run = start_result.task_run
            if start_result.outcome != "started":
                return _task_result_from_run(
                    task_run,
                    queue=registered.spec.queue,
                    idempotency=start_result.outcome,
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

    async def run_task_run(
        self,
        task_run: TaskRun,
        *,
        tenant_status: TenantStatus = "active",
    ) -> TaskResult:
        if self.task_repository is None:
            raise RuntimeError("task_repository is required to run a persisted task run")
        assert_tenant_operation_allowed(
            tenant_id=task_run.tenant_id,
            status=tenant_status,
            operation="task",
        )
        registered = self.task_registry.get(task_run.task_type)
        if task_run.status == "pending":
            await self.task_repository.start_pending(task_run)
        elif task_run.status in {"failed", "dead_letter"}:
            await self.task_repository.start_retry(task_run)
        elif task_run.status != "running":
            return _task_result_from_run(
                task_run,
                queue=registered.spec.queue,
                idempotency="replayed",
            )
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
            with use_background_context(
                task_background_context(
                    task_id=envelope.task_id,
                    task_type=envelope.task_type,
                    tenant_id=envelope.tenant_id,
                    request_id=envelope.request_id,
                    trace_id=envelope.trace_id,
                )
            ):
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


class DatabaseQueueTaskProvider:
    def __init__(
        self,
        task_registry: TaskRegistry,
        *,
        task_repository: TaskRunRepository,
        max_attempts: int = 3,
        retry_backoff_seconds: int = 30,
    ) -> None:
        self.task_registry = task_registry
        self.task_repository = task_repository
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

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
        enqueue_result = await self.task_repository.enqueue_once(
            envelope,
            queue=registered.spec.queue,
            max_attempts=self.max_attempts,
        )
        return _task_result_from_run(
            enqueue_result.task_run,
            queue=registered.spec.queue,
            idempotency=enqueue_result.outcome,
            provider="database-queue",
        )

    async def run_next(
        self,
        *,
        queue: str,
        tenant_status: TenantStatus = "active",
        now: datetime | None = None,
    ) -> TaskResult | None:
        task_run = await self.task_repository.claim_next_pending(queue=queue, now=now)
        if task_run is None:
            return None
        return await self.run_task_run(
            task_run,
            tenant_status=tenant_status,
            now=now,
        )

    async def run_task_run(
        self,
        task_run: TaskRun,
        *,
        tenant_status: TenantStatus = "active",
        now: datetime | None = None,
    ) -> TaskResult:
        assert_tenant_operation_allowed(
            tenant_id=task_run.tenant_id,
            status=tenant_status,
            operation="task",
        )
        registered = self.task_registry.get(task_run.task_type)
        if task_run.status == "pending":
            await self.task_repository.start_pending(task_run, now=now)
        elif task_run.status in {"succeeded", "dead_letter", "cancelled"}:
            return _task_result_from_run(
                task_run,
                queue=registered.spec.queue,
                idempotency="replayed",
                provider="database-queue",
            )
        elif task_run.status != "running":
            raise ValueError(f"Database queue cannot run task status {task_run.status!r}")
        envelope = self.task_repository.to_envelope(task_run)
        return await self._execute(
            envelope,
            handler=registered.handler,
            queue=registered.spec.queue,
            task_run=task_run,
            now=now,
        )

    async def _execute(
        self,
        envelope: TaskEnvelope,
        *,
        handler: TaskHandler,
        queue: str,
        task_run: TaskRun,
        now: datetime | None,
    ) -> TaskResult:
        try:
            with use_background_context(
                task_background_context(
                    task_id=envelope.task_id,
                    task_type=envelope.task_type,
                    tenant_id=envelope.tenant_id,
                    request_id=envelope.request_id,
                    trace_id=envelope.trace_id,
                )
            ):
                result = handler(envelope)
                if inspect.isawaitable(result):
                    result = await result
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            await self.task_repository.mark_retry_pending(
                task_run,
                error_message=error_message,
                retry_backoff_seconds=self.retry_backoff_seconds,
                now=now,
            )
            return TaskResult(
                task_id=envelope.task_id,
                task_type=envelope.task_type,
                status=_task_status(task_run.status),
                error_message=error_message,
                metadata={"provider": "database-queue", "queue": queue},
            )
        await self.task_repository.mark_succeeded(
            task_run,
            result_payload=result,
            now=now,
        )
        return TaskResult(
            task_id=envelope.task_id,
            task_type=envelope.task_type,
            status="succeeded",
            result_payload=result,
            metadata={"provider": "database-queue", "queue": queue},
        )


def _task_result_from_run(
    task_run: TaskRun,
    *,
    queue: str,
    idempotency: str,
    provider: str = "sync",
) -> TaskResult:
    return TaskResult(
        task_id=task_run.id,
        task_type=task_run.task_type,
        status=_task_status(task_run.status),
        result_payload=task_run.result_payload,
        error_message=task_run.error_message,
        metadata={"provider": provider, "queue": queue, "idempotency": idempotency},
    )


def _task_status(status: str) -> TaskStatus:
    if status in {"pending", "running", "succeeded", "failed", "dead_letter", "cancelled"}:
        return status  # type: ignore[return-value]
    raise ValueError(f"Unknown task run status: {status!r}")
