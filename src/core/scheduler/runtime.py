from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.apps import AppRegistry, resolve_runtime_capabilities
from core.audit import AuditRecorder
from core.config import Settings, get_settings
from core.db import unit_of_work
from core.locks import LockProvider, MemoryLockProvider
from core.operations import ProcessHeartbeatRepository
from core.scheduler.external import wrap_external_scheduler_provider
from core.scheduler.provider import (
    AuditedScheduleProvider,
    LockedScheduleProvider,
    ManualScheduleProvider,
    ScheduleTriggerRequest,
)
from core.scheduler.registry import ScheduleRegistry
from core.scheduler.repository import ScheduleStateRepository, ScheduleTriggerRepository
from core.tasks import DatabaseQueueTaskProvider, SyncTaskProvider, TaskRegistry, TaskRunRepository


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
    provider: str = "local",
    lock_provider: LockProvider | None = None,
    audit_factory: Callable[[AsyncSession], AuditRecorder] | None = None,
) -> SchedulerRunResult:
    settings = get_settings()
    app_registry = AppRegistry(
        module_paths,
        runtime_capabilities=resolve_runtime_capabilities(
            settings,
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
    resolved_lock_provider = lock_provider or MemoryLockProvider()
    iterations = 0
    checked = 0
    triggered = 0
    failed = 0
    schedule_results: list[dict[str, object]] = []
    try:
        while max_iterations is None or iterations < max_iterations:
            planned_at = _planned_at(now or datetime.now(UTC))
            async with unit_of_work(session_factory) as uow:
                if uow.session is None:
                    raise RuntimeError("database session was not initialized")
                state_repository = ScheduleStateRepository(uow.session)
                trigger_provider = LockedScheduleProvider(
                    provider=ManualScheduleProvider(
                        schedule_registry=schedule_registry,
                        task_provider=_task_provider(
                            settings=settings,
                            task_registry=task_registry,
                            repository=TaskRunRepository(uow.session),
                        ),
                        trigger_repository=ScheduleTriggerRepository(uow.session),
                    ),
                    lock_provider=resolved_lock_provider,
                    lock_ttl_seconds=lock_ttl_seconds,
                )
                trigger_provider = wrap_external_scheduler_provider(
                    provider=provider,
                    schedule_registry=schedule_registry,
                    trigger_provider=trigger_provider,
                )
                if audit_factory is not None:
                    trigger_provider = AuditedScheduleProvider(
                        provider=trigger_provider,
                        audit=audit_factory(uow.session),
                    )
                checked += len(schedule_registry.registered_schedules)
                for registered in schedule_registry.registered_schedules:
                    if registered.spec.trigger != "cron":
                        continue
                    plan = await state_repository.plan_cron_due_slots(
                        schedule_id=registered.spec.schedule_id,
                        tenant_id=tenant_id,
                        trigger_config=registered.spec.trigger_config,
                        misfire_policy=registered.spec.misfire_policy,
                        now=planned_at,
                    )
                    if plan.skipped_until is not None:
                        await state_repository.mark_skipped_until(
                            schedule_id=registered.spec.schedule_id,
                            tenant_id=tenant_id,
                            planned_at=plan.skipped_until,
                            checked_at=planned_at,
                        )
                    for slot_planned_at in plan.planned_slots:
                        try:
                            result = await trigger_provider.trigger(
                                ScheduleTriggerRequest(
                                    schedule_id=registered.spec.schedule_id,
                                    tenant_id=tenant_id,
                                    request_id=_request_id(
                                        request_id_prefix=request_id_prefix,
                                        schedule_id=registered.spec.schedule_id,
                                        planned_at=slot_planned_at,
                                    ),
                                    planned_at=slot_planned_at,
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
                                    "planned_at": slot_planned_at.isoformat(),
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            )
                            continue
                        triggered += 1
                        if not result.ok:
                            failed += 1
                        await state_repository.mark_triggered(
                            schedule_id=registered.spec.schedule_id,
                            tenant_id=tenant_id,
                            planned_at=slot_planned_at,
                            triggered_at=planned_at,
                        )
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


def _task_provider(
    *,
    settings: Settings,
    task_registry: TaskRegistry,
    repository: TaskRunRepository,
) -> SyncTaskProvider | DatabaseQueueTaskProvider:
    if settings.task_queue.provider == "database":
        return DatabaseQueueTaskProvider(
            task_registry,
            task_repository=repository,
            max_attempts=settings.task_queue.max_attempts,
            retry_backoff_seconds=settings.task_queue.retry_backoff_seconds,
        )
    return SyncTaskProvider(
        task_registry,
        task_repository=repository,
        max_attempts=settings.task_queue.max_attempts,
    )


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
