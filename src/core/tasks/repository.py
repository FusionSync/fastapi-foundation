from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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
        max_attempts: int = 1,
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
        task_run.status = "failed"
        task_run.error_message = error_message
        task_run.finished_at = now or datetime.now(UTC)
        await self.session.flush()
