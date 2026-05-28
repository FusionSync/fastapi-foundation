from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.scheduler.models import ScheduleTriggerLog

ScheduleTriggerHistoryOutcome = Literal["started", "replayed"]


@dataclass(frozen=True, slots=True)
class ScheduleTriggerHistoryResult:
    outcome: ScheduleTriggerHistoryOutcome
    log: ScheduleTriggerLog


class ScheduleTriggerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_result(
        self,
        *,
        schedule_id: str,
        tenant_id: str,
        task_id: str,
        task_type: str,
        planned_at: datetime,
        triggered_at: datetime,
        request_id: str,
        status: str,
        error_message: str | None,
        details: dict[str, Any] | None = None,
    ) -> ScheduleTriggerHistoryResult:
        log = ScheduleTriggerLog(
            schedule_id=schedule_id,
            tenant_id=tenant_id,
            task_id=task_id,
            task_type=task_type,
            planned_at=planned_at,
            triggered_at=triggered_at,
            request_id=request_id,
            status=status,
            error_message=error_message,
            details=details or {},
        )
        try:
            async with self.session.begin_nested():
                self.session.add(log)
                await self.session.flush()
        except IntegrityError:
            existing = await self._get_by_trigger_key(
                schedule_id=schedule_id,
                tenant_id=tenant_id,
                planned_at=planned_at,
            )
            if existing is None:
                raise AppError(
                    "CONFLICT",
                    "Schedule trigger history raced but no record was found",
                    status_code=409,
                ) from None
            self._assert_same_trigger(existing, task_id=task_id, task_type=task_type)
            return ScheduleTriggerHistoryResult(outcome="replayed", log=existing)
        return ScheduleTriggerHistoryResult(outcome="started", log=log)

    async def _get_by_trigger_key(
        self,
        *,
        schedule_id: str,
        tenant_id: str,
        planned_at: datetime,
    ) -> ScheduleTriggerLog | None:
        result = await self.session.execute(
            select(ScheduleTriggerLog)
            .where(ScheduleTriggerLog.schedule_id == schedule_id)
            .where(ScheduleTriggerLog.tenant_id == tenant_id)
            .where(ScheduleTriggerLog.planned_at == planned_at)
        )
        return result.scalars().first()

    def _assert_same_trigger(
        self,
        log: ScheduleTriggerLog,
        *,
        task_id: str,
        task_type: str,
    ) -> None:
        if log.task_id == task_id and log.task_type == task_type:
            return
        raise AppError(
            "SCHEDULE_TRIGGER_CONFLICT",
            "Schedule trigger key was already used with a different task",
            status_code=409,
            details={
                "schedule_id": log.schedule_id,
                "tenant_id": log.tenant_id,
                "planned_at": log.planned_at.isoformat(),
                "existing_task_id": log.task_id,
                "requested_task_id": task_id,
            },
        )
