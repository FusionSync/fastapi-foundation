from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.apps import AppRegistry, ScheduleSpec, resolve_runtime_capabilities
from core.config import get_settings
from core.db import unit_of_work
from core.locks import MemoryLockProvider
from core.operations import ProcessHeartbeatRepository
from core.scheduler.provider import (
    LockedScheduleProvider,
    ManualScheduleProvider,
    ScheduleTriggerRequest,
)
from core.scheduler.registry import RegisteredSchedule, ScheduleRegistry
from core.scheduler.repository import ScheduleTriggerRepository
from core.tasks import SyncTaskProvider, TaskRegistry, TaskRunRepository


@dataclass(frozen=True, slots=True)
class SchedulerRunResult:
    ok: bool
    tenant_id: str
    iterations: int
    checked: int = 0
    triggered: int = 0
    failed: int = 0
    instance_id: str | None = None
    schedule_results: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "tenant_id": self.tenant_id,
            "iterations": self.iterations,
            "checked": self.checked,
            "triggered": self.triggered,
            "failed": self.failed,
            "schedule_results": self.schedule_results,
        }
        if self.instance_id is not None:
            payload["instance_id"] = self.instance_id
        return payload


async def run_scheduler_loop(
    *,
    database_url: str,
    module_paths: list[str],
    tenant_id: str,
    tenant_status: str,
    request_id_prefix: str,
    payload: dict[str, object] | None = None,
    now: datetime | None = None,
    instance_id: str | None = None,
    max_iterations: int | None = None,
    idle_sleep_seconds: float = 1.0,
    lock_ttl_seconds: int = 60,
) -> SchedulerRunResult:
    app_registry = AppRegistry(
        module_paths,
        runtime_capabilities=resolve_runtime_capabilities(
            get_settings(),
            database_url=database_url,
            service_role="scheduler",
        ),
    ).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    schedule_registry = ScheduleRegistry.from_app_registry(
        app_registry,
        task_registry=task_registry,
    )
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    lock_provider = MemoryLockProvider()
    iterations = 0
    checked = 0
    triggered = 0
    failed = 0
    seen_slots: set[tuple[str, str, datetime]] = set()
    schedule_results: list[dict[str, object]] = []
    try:
        while max_iterations is None or iterations < max_iterations:
            planned_at = _planned_at(now or datetime.now(UTC))
            due_schedules = _due_schedules(schedule_registry, planned_at)
            async with unit_of_work(session_factory) as uow:
                if uow.session is None:
                    raise RuntimeError("database session was not initialized")
                provider = LockedScheduleProvider(
                    provider=ManualScheduleProvider(
                        schedule_registry=schedule_registry,
                        task_provider=SyncTaskProvider(
                            task_registry,
                            task_repository=TaskRunRepository(uow.session),
                        ),
                        trigger_repository=ScheduleTriggerRepository(uow.session),
                    ),
                    lock_provider=lock_provider,
                    lock_ttl_seconds=lock_ttl_seconds,
                )
                checked += len(schedule_registry.registered_schedules)
                for registered in due_schedules:
                    slot_key = (registered.spec.schedule_id, tenant_id, planned_at)
                    if slot_key in seen_slots:
                        continue
                    seen_slots.add(slot_key)
                    try:
                        result = await provider.trigger(
                            ScheduleTriggerRequest(
                                schedule_id=registered.spec.schedule_id,
                                tenant_id=tenant_id,
                                request_id=_request_id(
                                    request_id_prefix=request_id_prefix,
                                    schedule_id=registered.spec.schedule_id,
                                    planned_at=planned_at,
                                ),
                                planned_at=planned_at,
                                payload=dict(payload or {}),
                            ),
                            tenant_status=tenant_status,  # type: ignore[arg-type]
                        )
                    except Exception as exc:
                        failed += 1
                        schedule_results.append(
                            {
                                "ok": False,
                                "schedule_id": registered.spec.schedule_id,
                                "planned_at": planned_at.isoformat(),
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        continue
                    triggered += 1
                    if not result.ok:
                        failed += 1
                    schedule_results.append(result.to_dict())
                iterations += 1
                if instance_id is not None:
                    await ProcessHeartbeatRepository(uow.session).record(
                        role="scheduler",
                        instance_id=instance_id,
                        details={
                            "tenant_id": tenant_id,
                            "iterations": iterations,
                            "checked": checked,
                            "triggered": triggered,
                            "failed": failed,
                        },
                    )
            if max_iterations is None:
                await asyncio.sleep(idle_sleep_seconds)
    finally:
        await engine.dispose()

    return SchedulerRunResult(
        ok=failed == 0,
        tenant_id=tenant_id,
        iterations=iterations,
        checked=checked,
        triggered=triggered,
        failed=failed,
        instance_id=instance_id,
        schedule_results=schedule_results,
    )


def _due_schedules(
    schedule_registry: ScheduleRegistry,
    planned_at: datetime,
) -> list[RegisteredSchedule]:
    return [
        registered
        for registered in schedule_registry.registered_schedules
        if _is_due(registered.spec, planned_at)
    ]


def _is_due(spec: ScheduleSpec, planned_at: datetime) -> bool:
    if spec.trigger != "cron":
        return False
    return _cron_matches(
        spec.trigger_config.get("hour", "*"),
        actual=planned_at.hour,
        minimum=0,
        maximum=23,
    ) and _cron_matches(
        spec.trigger_config.get("minute", "0"),
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
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(second=0, microsecond=0)


def _request_id(
    *,
    request_id_prefix: str,
    schedule_id: str,
    planned_at: datetime,
) -> str:
    return f"{request_id_prefix}:{schedule_id}:{planned_at.isoformat()}"
