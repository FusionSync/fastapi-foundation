from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.tasks.models import TaskRun
from core.tasks.registry import TaskEnvelope

TaskStartOutcome = Literal["started", "replayed", "in_progress"]


@dataclass(frozen=True, slots=True)
class TaskStartResult:
    outcome: TaskStartOutcome
    task_run: TaskRun


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
        return (
            await self.start_once(
                envelope,
                queue=queue,
                max_attempts=max_attempts,
                now=now,
            )
        ).task_run

    async def start_once(
        self,
        envelope: TaskEnvelope,
        *,
        queue: str,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> TaskStartResult:
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
            trace_id=envelope.trace_id,
            started_at=resolved_now,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(task_run)
                await self.session.flush()
        except IntegrityError:
            existing = await self._get_by_idempotency_key(
                tenant_id=envelope.tenant_id,
                idempotency_key=envelope.idempotency_key,
            )
            if existing is None:
                raise AppError(
                    "CONFLICT",
                    "Task idempotency claim raced but no task run was found",
                    status_code=409,
                ) from None
            self._assert_same_idempotent_request(existing, envelope)
            outcome: TaskStartOutcome = (
                "in_progress" if existing.status in {"pending", "running"} else "replayed"
            )
            return TaskStartResult(outcome=outcome, task_run=existing)
        return TaskStartResult(outcome="started", task_run=task_run)

    async def get(self, task_id: str) -> TaskRun | None:
        return await self.session.get(TaskRun, task_id)

    async def claim_next_pending(
        self,
        *,
        queue: str | None = None,
        now: datetime | None = None,
    ) -> TaskRun | None:
        statement = (
            select(TaskRun)
            .where(TaskRun.status == "pending")
            .order_by(TaskRun.created_at.asc(), TaskRun.id.asc())
            .limit(1)
        )
        if queue is not None:
            statement = statement.where(TaskRun.queue == queue)
        result = await self.session.execute(statement)
        task_run = result.scalars().first()
        if task_run is None:
            return None
        await self.start_pending(task_run, now=now)
        return task_run

    async def start_pending(
        self,
        task_run: TaskRun,
        *,
        now: datetime | None = None,
    ) -> None:
        if task_run.status != "pending":
            raise AppError(
                "CONFLICT",
                "Only pending task runs can be claimed by a worker",
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

    async def _get_by_idempotency_key(
        self,
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> TaskRun | None:
        result = await self.session.execute(
            select(TaskRun)
            .where(TaskRun.tenant_id == tenant_id)
            .where(TaskRun.idempotency_key == idempotency_key)
        )
        return result.scalars().first()

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

    async def recover_stale_running(
        self,
        *,
        older_than: datetime,
        limit: int = 50,
        error_message: str = "Task run recovered after worker interruption",
        now: datetime | None = None,
    ) -> list[TaskRun]:
        resolved_now = now or datetime.now(UTC)
        result = await self.session.execute(
            select(TaskRun)
            .where(TaskRun.status == "running")
            .where(TaskRun.started_at < older_than)
            .order_by(TaskRun.started_at.asc())
            .limit(limit)
        )
        task_runs = list(result.scalars().all())
        for task_run in task_runs:
            task_run.status = (
                "dead_letter" if task_run.attempt_count >= task_run.max_attempts else "failed"
            )
            task_run.error_message = error_message
            task_run.finished_at = resolved_now
        await self.session.flush()
        return task_runs

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
            trace_id=task_run.trace_id,
        )

    def _assert_same_idempotent_request(
        self,
        task_run: TaskRun,
        envelope: TaskEnvelope,
    ) -> None:
        if task_run.task_type == envelope.task_type and task_run.input_payload == envelope.payload:
            return
        raise AppError(
            "TASK_IDEMPOTENCY_KEY_CONFLICT",
            "Task idempotency key was already used with a different task request",
            status_code=409,
            details={
                "tenant_id": envelope.tenant_id,
                "idempotency_key": envelope.idempotency_key,
                "existing_task_id": task_run.id,
                "existing_task_type": task_run.task_type,
                "requested_task_type": envelope.task_type,
            },
        )
