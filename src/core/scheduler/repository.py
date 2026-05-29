from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.scheduler.models import ScheduleState, ScheduleTriggerLog

ScheduleTriggerHistoryOutcome = Literal["started", "replayed"]


@dataclass(frozen=True, slots=True)
class ScheduleTriggerHistoryResult:
    outcome: ScheduleTriggerHistoryOutcome
    log: ScheduleTriggerLog


@dataclass(frozen=True, slots=True)
class ScheduleDuePlan:
    planned_slots: list[datetime]
    skipped_until: datetime | None = None


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


class ScheduleStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def plan_cron_due_slots(
        self,
        *,
        schedule_id: str,
        tenant_id: str,
        trigger_config: dict[str, str],
        misfire_policy: str,
        now: datetime,
    ) -> ScheduleDuePlan:
        current = _planned_at(now)
        state = await self.get(schedule_id=schedule_id, tenant_id=tenant_id)
        last_planned_at = _ensure_aware(state.last_planned_at) if state else None
        due_slots = _cron_due_slots(
            trigger_config=trigger_config,
            after=last_planned_at,
            until=current,
        )
        if misfire_policy == "skip":
            if due_slots and due_slots[-1] == current:
                return ScheduleDuePlan(planned_slots=[current])
            return ScheduleDuePlan(
                planned_slots=[],
                skipped_until=due_slots[-1] if due_slots else None,
            )
        if misfire_policy == "run_once":
            return ScheduleDuePlan(planned_slots=due_slots[-1:])
        if misfire_policy == "catch_up_limited":
            limit = max(1, int(trigger_config.get("misfire_limit", "3")))
            return ScheduleDuePlan(planned_slots=due_slots[:limit])
        raise ValueError(f"Unknown misfire policy: {misfire_policy!r}")

    async def mark_triggered(
        self,
        *,
        schedule_id: str,
        tenant_id: str,
        planned_at: datetime,
        triggered_at: datetime,
    ) -> ScheduleState:
        return await self._upsert_state(
            schedule_id=schedule_id,
            tenant_id=tenant_id,
            last_planned_at=planned_at,
            last_triggered_at=triggered_at,
            last_checked_at=triggered_at,
        )

    async def mark_skipped_until(
        self,
        *,
        schedule_id: str,
        tenant_id: str,
        planned_at: datetime,
        checked_at: datetime,
    ) -> ScheduleState:
        return await self._upsert_state(
            schedule_id=schedule_id,
            tenant_id=tenant_id,
            last_planned_at=planned_at,
            last_checked_at=checked_at,
        )

    async def get(self, *, schedule_id: str, tenant_id: str) -> ScheduleState | None:
        result = await self.session.execute(
            select(ScheduleState)
            .where(ScheduleState.schedule_id == schedule_id)
            .where(ScheduleState.tenant_id == tenant_id)
        )
        return result.scalars().first()

    async def _upsert_state(
        self,
        *,
        schedule_id: str,
        tenant_id: str,
        last_planned_at: datetime,
        last_checked_at: datetime,
        last_triggered_at: datetime | None = None,
    ) -> ScheduleState:
        state = await self.get(schedule_id=schedule_id, tenant_id=tenant_id)
        if state is None:
            state = ScheduleState(
                schedule_id=schedule_id,
                tenant_id=tenant_id,
                last_planned_at=last_planned_at,
                last_triggered_at=last_triggered_at,
                last_checked_at=last_checked_at,
            )
            self.session.add(state)
        else:
            state.last_planned_at = last_planned_at
            if last_triggered_at is not None:
                state.last_triggered_at = last_triggered_at
            state.last_checked_at = last_checked_at
        await self.session.flush()
        return state


def _cron_due_slots(
    *,
    trigger_config: dict[str, str],
    after: datetime | None,
    until: datetime,
) -> list[datetime]:
    if after is None:
        return [until] if _cron_matches_config(trigger_config, until) else []
    cursor = _planned_at(after) + timedelta(minutes=1)
    slots: list[datetime] = []
    while cursor <= until:
        if _cron_matches_config(trigger_config, cursor):
            slots.append(cursor)
        cursor += timedelta(minutes=1)
    return slots


def _cron_matches_config(trigger_config: dict[str, str], planned_at: datetime) -> bool:
    return _cron_matches(
        trigger_config.get("hour", "*"),
        actual=planned_at.hour,
        minimum=0,
        maximum=23,
    ) and _cron_matches(
        trigger_config.get("minute", "0"),
        actual=planned_at.minute,
        minimum=0,
        maximum=59,
    )


def _cron_matches(value: str, *, actual: int, minimum: int, maximum: int) -> bool:
    if value == "*":
        return True
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        expected = int(item)
        if expected < minimum or expected > maximum:
            raise ValueError(f"cron value {expected} out of range {minimum}-{maximum}")
        if expected == actual:
            return True
    return False


def _planned_at(value: datetime) -> datetime:
    value = _ensure_aware(value)
    if value is None:
        raise ValueError("planned datetime is required")
    return value.astimezone(UTC).replace(second=0, microsecond=0)


def _ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
