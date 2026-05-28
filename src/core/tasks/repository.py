from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.tasks.models import TaskRun
from core.tasks.registry import TaskEnvelope


class TaskRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(
        self,
        envelope: TaskEnvelope,
        *,
        queue: str,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> TaskRun:
        resolved_now = now or datetime.now(UTC)
        task_run = TaskRun(
            id=envelope.task_id,
            tenant_id=envelope.tenant_id,
            task_type=envelope.task_type,
            idempotency_key=envelope.idempotency_key,
            status="running",
            progress=0,
            input_payload=envelope.payload,
            queue=queue,
            attempt_count=1,
            max_attempts=max_attempts,
            request_id=envelope.request_id,
            started_at=resolved_now,
        )
        self.session.add(task_run)
        await self.session.flush()
        return task_run

    async def get(self, task_id: str) -> TaskRun | None:
        return await self.session.get(TaskRun, task_id)

    async def require(self, task_id: str) -> TaskRun:
        task_run = await self.get(task_id)
        if task_run is None:
            raise AppError("NOT_FOUND", f"TaskRun {task_id!r} not found", status_code=404)
        return task_run

    async def list_failed(self, *, limit: int = 50) -> list[TaskRun]:
        result = await self.session.execute(
            select(TaskRun)
            .where(TaskRun.status.in_(["failed", "dead_letter"]))
            .order_by(TaskRun.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def start_retry(
        self,
        task_run: TaskRun,
        *,
        now: datetime | None = None,
    ) -> None:
        if task_run.status not in {"failed", "dead_letter"}:
            raise AppError(
                "CONFLICT",
                "Only failed or dead-letter task runs can be retried",
                status_code=409,
            )
        task_run.status = "running"
        task_run.progress = 0
        task_run.attempt_count += 1
        task_run.result_payload = None
        task_run.error_message = None
        task_run.started_at = now or datetime.now(UTC)
        task_run.finished_at = None
        await self.session.flush()

    async def mark_succeeded(
        self,
        task_run: TaskRun,
        *,
        result_payload: dict[str, Any] | None,
        now: datetime | None = None,
    ) -> None:
        task_run.status = "succeeded"
        task_run.progress = 100
        task_run.result_payload = result_payload
        task_run.error_message = None
        task_run.finished_at = now or datetime.now(UTC)
        await self.session.flush()

    async def mark_failed(
        self,
        task_run: TaskRun,
        *,
        error_message: str,
        now: datetime | None = None,
    ) -> None:
        task_run.status = (
            "dead_letter" if task_run.attempt_count >= task_run.max_attempts else "failed"
        )
        task_run.error_message = error_message
        task_run.finished_at = now or datetime.now(UTC)
        await self.session.flush()

    def to_envelope(self, task_run: TaskRun) -> TaskEnvelope:
        return TaskEnvelope(
            task_id=task_run.id,
            task_type=task_run.task_type,
            tenant_id=task_run.tenant_id,
            payload=task_run.input_payload,
            idempotency_key=task_run.idempotency_key,
            request_id=task_run.request_id or "",
        )
