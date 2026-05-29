import sys
import types
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.apps import AppModule, AppRegistry, ScheduleSpec, TaskHandlerSpec
from core.base.models import BaseModel
from core.context import (
    RequestContext,
    get_current_context,
    reset_current_context,
    set_current_context,
)
from core.db import unit_of_work
from core.exceptions import AppError
from core.locks import MemoryLockProvider
from core.scheduler import (
    LockedScheduleProvider,
    ManualScheduleProvider,
    ScheduleRegistry,
    ScheduleState,
    ScheduleStateRepository,
    ScheduleTriggerLog,
    ScheduleTriggerRepository,
    ScheduleTriggerRequest,
)
from core.tasks import SyncTaskProvider, TaskEnvelope, TaskRegistry, TaskResult, TaskRunRepository


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_task_registry_sync_provider_and_schedule_registry_connect_app_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        return {
            "tenant_id": envelope.tenant_id,
            "value": envelope.payload["value"],
        }

    _install_handler_module(monkeypatch, refresh=refresh)
    _install_app(
        monkeypatch,
        label="task_app",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_handlers.refresh",
                queue="default",
            )
        ],
        schedules=[
            ScheduleSpec(
                schedule_id="example.refresh.daily",
                task_type="example.refresh",
                trigger="cron",
                trigger_config={"hour": "1"},
            )
        ],
    )

    app_registry = AppRegistry(["fake_task_app"]).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    schedule_registry = ScheduleRegistry.from_app_registry(
        app_registry,
        task_registry=task_registry,
    )
    result = await SyncTaskProvider(task_registry).submit(
        TaskEnvelope(
            task_id="task-1",
            task_type="example.refresh",
            tenant_id="tenant-a",
            payload={"value": "ok"},
            idempotency_key="example.refresh:tenant-a",
            request_id="req-1",
        )
    )

    assert task_registry.task_types == {"example.refresh"}
    assert schedule_registry.schedule_ids == {"example.refresh.daily"}
    assert result.ok is True
    assert result.result_payload == {"tenant_id": "tenant-a", "value": "ok"}
    assert result.metadata == {"provider": "sync", "queue": "default"}


@pytest.mark.asyncio
async def test_manual_schedule_provider_triggers_registered_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        return {
            "idempotency_key": envelope.idempotency_key,
            "request_id": envelope.request_id,
            "tenant_id": envelope.tenant_id,
            "value": envelope.payload["value"],
        }

    _install_handler_module(monkeypatch, refresh=refresh)
    _install_app(
        monkeypatch,
        label="task_app",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_handlers.refresh",
                queue="maintenance",
            )
        ],
        schedules=[
            ScheduleSpec(
                schedule_id="example.refresh.daily",
                task_type="example.refresh",
                trigger="cron",
                trigger_config={"hour": "1"},
            )
        ],
    )
    app_registry = AppRegistry(["fake_task_app"]).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    schedule_registry = ScheduleRegistry.from_app_registry(
        app_registry,
        task_registry=task_registry,
    )
    planned_at = datetime(2026, 5, 28, 1, 0, tzinfo=UTC)

    result = await ManualScheduleProvider(
        schedule_registry=schedule_registry,
        task_provider=SyncTaskProvider(task_registry),
    ).trigger(
        ScheduleTriggerRequest(
            schedule_id="example.refresh.daily",
            tenant_id="tenant-a",
            payload={"value": "ok"},
            request_id="req-1",
            planned_at=planned_at,
        )
    )

    assert result.ok is True
    assert result.schedule_id == "example.refresh.daily"
    assert result.task_type == "example.refresh"
    assert result.planned_at == planned_at
    assert result.task_result.result_payload == {
        "idempotency_key": "schedule:example.refresh.daily:tenant-a:2026-05-28T01:00:00+00:00",
        "request_id": "req-1",
        "tenant_id": "tenant-a",
        "value": "ok",
    }
    assert result.task_result.metadata == {"provider": "sync", "queue": "maintenance"}


@pytest.mark.asyncio
async def test_manual_schedule_provider_sets_background_context_for_submit_and_resets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_contexts: list[RequestContext | None] = []

    class RecordingSubmitter:
        async def submit(
            self,
            envelope: TaskEnvelope,
            *,
            tenant_status: str = "active",
        ) -> TaskResult:
            seen_contexts.append(get_current_context())
            return TaskResult(
                task_id=envelope.task_id,
                task_type=envelope.task_type,
                status="succeeded",
            )

    _install_handler_module(monkeypatch, refresh=lambda envelope: None)
    _install_app(
        monkeypatch,
        label="task_app",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_handlers.refresh",
            )
        ],
        schedules=[
            ScheduleSpec(
                schedule_id="example.refresh.daily",
                task_type="example.refresh",
                trigger="cron",
                trigger_config={"hour": "1"},
            )
        ],
    )
    app_registry = AppRegistry(["fake_task_app"]).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    schedule_registry = ScheduleRegistry.from_app_registry(
        app_registry,
        task_registry=task_registry,
    )
    outer_context = RequestContext(
        request_id="req-outer",
        tenant_id="tenant-outer",
    ).freeze()
    token = set_current_context(outer_context)
    try:
        await ManualScheduleProvider(
            schedule_registry=schedule_registry,
            task_provider=RecordingSubmitter(),
        ).trigger(
            ScheduleTriggerRequest(
                schedule_id="example.refresh.daily",
                tenant_id="tenant-a",
                request_id="req-schedule",
                trace_id="trace-schedule",
                planned_at=datetime(2026, 5, 28, 1, 0, tzinfo=UTC),
            )
        )

        assert get_current_context() == outer_context
    finally:
        reset_current_context(token)

    assert len(seen_contexts) == 1
    context = seen_contexts[0]
    assert context is not None
    assert context.request_id == "req-schedule"
    assert context.trace_id == "trace-schedule"
    assert context.tenant_id == "tenant-a"
    assert context.route == "scheduler:example.refresh.daily"
    assert context.method == "SCHEDULER"
    assert context.frozen is True


@pytest.mark.asyncio
async def test_manual_schedule_provider_records_trigger_history_and_replays_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    calls: list[str] = []

    async def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        calls.append(envelope.task_id)
        return {"task_id": envelope.task_id}

    _install_handler_module(monkeypatch, refresh=refresh)
    _install_app(
        monkeypatch,
        label="task_app",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_handlers.refresh",
                queue="maintenance",
            )
        ],
        schedules=[
            ScheduleSpec(
                schedule_id="example.refresh.daily",
                task_type="example.refresh",
                trigger="cron",
                trigger_config={"hour": "1"},
            )
        ],
    )
    app_registry = AppRegistry(["fake_task_app"]).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    schedule_registry = ScheduleRegistry.from_app_registry(
        app_registry,
        task_registry=task_registry,
    )
    planned_at = datetime(2026, 5, 28, 1, 0, tzinfo=UTC)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        first = await ManualScheduleProvider(
            schedule_registry=schedule_registry,
            task_provider=SyncTaskProvider(
                task_registry,
                task_repository=TaskRunRepository(uow.session),
            ),
            trigger_repository=ScheduleTriggerRepository(uow.session),
        ).trigger(
            ScheduleTriggerRequest(
                schedule_id="example.refresh.daily",
                tenant_id="tenant-a",
                payload={"value": "ok"},
                request_id="req-1",
                planned_at=planned_at,
            )
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        replayed = await ManualScheduleProvider(
            schedule_registry=schedule_registry,
            task_provider=SyncTaskProvider(
                task_registry,
                task_repository=TaskRunRepository(uow.session),
            ),
            trigger_repository=ScheduleTriggerRepository(uow.session),
        ).trigger(
            ScheduleTriggerRequest(
                schedule_id="example.refresh.daily",
                tenant_id="tenant-a",
                payload={"value": "ok"},
                request_id="req-2",
                planned_at=planned_at,
            )
        )

    logs = await _trigger_logs(session_factory)
    assert calls == [first.task_id]
    assert replayed.task_id == first.task_id
    assert replayed.metadata["trigger_history"] == "replayed"
    assert replayed.task_result.metadata["idempotency"] == "replayed"
    assert [(log.schedule_id, log.tenant_id, log.task_id, log.status) for log in logs] == [
        ("example.refresh.daily", "tenant-a", first.task_id, "succeeded")
    ]


@pytest.mark.asyncio
async def test_schedule_state_repository_applies_misfire_policies(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    last_planned_at = datetime(2026, 5, 28, 1, 0, tzinfo=UTC)
    now = datetime(2026, 5, 28, 4, 30, tzinfo=UTC)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add_all(
            [
                ScheduleState(
                    schedule_id="skip.hourly",
                    tenant_id="tenant-a",
                    last_planned_at=last_planned_at,
                ),
                ScheduleState(
                    schedule_id="run-once.hourly",
                    tenant_id="tenant-a",
                    last_planned_at=last_planned_at,
                ),
                ScheduleState(
                    schedule_id="catch-up.hourly",
                    tenant_id="tenant-a",
                    last_planned_at=last_planned_at,
                ),
            ]
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        repository = ScheduleStateRepository(uow.session)
        skip_plan = await repository.plan_cron_due_slots(
            schedule_id="skip.hourly",
            tenant_id="tenant-a",
            trigger_config={"minute": "0"},
            misfire_policy="skip",
            now=now,
        )
        run_once_plan = await repository.plan_cron_due_slots(
            schedule_id="run-once.hourly",
            tenant_id="tenant-a",
            trigger_config={"minute": "0"},
            misfire_policy="run_once",
            now=now,
        )
        catch_up_plan = await repository.plan_cron_due_slots(
            schedule_id="catch-up.hourly",
            tenant_id="tenant-a",
            trigger_config={"minute": "0", "misfire_limit": "2"},
            misfire_policy="catch_up_limited",
            now=now,
        )

    assert skip_plan.planned_slots == []
    assert skip_plan.skipped_until == datetime(2026, 5, 28, 4, 0, tzinfo=UTC)
    assert run_once_plan.planned_slots == [datetime(2026, 5, 28, 4, 0, tzinfo=UTC)]
    assert catch_up_plan.planned_slots == [
        datetime(2026, 5, 28, 2, 0, tzinfo=UTC),
        datetime(2026, 5, 28, 3, 0, tzinfo=UTC),
    ]


@pytest.mark.asyncio
async def test_schedule_state_repository_marks_skipped_slots_persistent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        repository = ScheduleStateRepository(uow.session)
        await repository.mark_skipped_until(
            schedule_id="skip.hourly",
            tenant_id="tenant-a",
            planned_at=datetime(2026, 5, 28, 4, 0, tzinfo=UTC),
            checked_at=datetime(2026, 5, 28, 4, 30, tzinfo=UTC),
        )

    state = await _schedule_state(session_factory, "skip.hourly", "tenant-a")
    assert state is not None
    assert state.last_planned_at == datetime(2026, 5, 28, 4, 0)
    assert state.last_checked_at == datetime(2026, 5, 28, 4, 30)


@pytest.mark.asyncio
async def test_manual_schedule_provider_preserves_tenant_lifecycle_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        return {"task_id": envelope.task_id}

    _install_handler_module(monkeypatch, refresh=refresh)
    _install_app(
        monkeypatch,
        label="task_app",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_handlers.refresh",
            )
        ],
        schedules=[
            ScheduleSpec(
                schedule_id="example.refresh.daily",
                task_type="example.refresh",
                trigger="cron",
                trigger_config={"hour": "1"},
            )
        ],
    )
    app_registry = AppRegistry(["fake_task_app"]).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    schedule_registry = ScheduleRegistry.from_app_registry(
        app_registry,
        task_registry=task_registry,
    )

    with pytest.raises(AppError) as exc_info:
        await ManualScheduleProvider(
            schedule_registry=schedule_registry,
            task_provider=SyncTaskProvider(task_registry),
        ).trigger(
            ScheduleTriggerRequest(
                schedule_id="example.refresh.daily",
                tenant_id="tenant-a",
                payload={},
                request_id="req-1",
                planned_at=datetime(2026, 5, 28, 1, 0, tzinfo=UTC),
            ),
            tenant_status="suspended",
        )

    assert exc_info.value.code == "TENANT_STATE_FORBIDDEN"


@pytest.mark.asyncio
async def test_locked_schedule_provider_rejects_trigger_when_leader_lock_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        return {"task_id": envelope.task_id}

    _install_handler_module(monkeypatch, refresh=refresh)
    _install_app(
        monkeypatch,
        label="task_app",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_handlers.refresh",
            )
        ],
        schedules=[
            ScheduleSpec(
                schedule_id="example.refresh.daily",
                task_type="example.refresh",
                trigger="cron",
                trigger_config={"hour": "1"},
            )
        ],
    )
    app_registry = AppRegistry(["fake_task_app"]).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    schedule_registry = ScheduleRegistry.from_app_registry(
        app_registry,
        task_registry=task_registry,
    )
    locks = MemoryLockProvider()
    await locks.acquire(
        "scheduler:trigger:example.refresh.daily:tenant-a:2026-05-28T01:00:00+00:00",
        ttl_seconds=60,
        owner_token="other-scheduler",
    )

    with pytest.raises(AppError) as exc_info:
        await LockedScheduleProvider(
            provider=ManualScheduleProvider(
                schedule_registry=schedule_registry,
                task_provider=SyncTaskProvider(task_registry),
            ),
            lock_provider=locks,
        ).trigger(
            ScheduleTriggerRequest(
                schedule_id="example.refresh.daily",
                tenant_id="tenant-a",
                payload={},
                request_id="req-1",
                planned_at=datetime(2026, 5, 28, 1, 0, tzinfo=UTC),
            )
        )

    assert exc_info.value.code == "LOCK_NOT_ACQUIRED"
    assert exc_info.value.details == {
        "lock_key": "scheduler:trigger:example.refresh.daily:tenant-a:2026-05-28T01:00:00+00:00"
    }


@pytest.mark.asyncio
async def test_locked_schedule_provider_releases_trigger_lock_after_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        return {"task_id": envelope.task_id}

    _install_handler_module(monkeypatch, refresh=refresh)
    _install_app(
        monkeypatch,
        label="task_app",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_handlers.refresh",
            )
        ],
        schedules=[
            ScheduleSpec(
                schedule_id="example.refresh.daily",
                task_type="example.refresh",
                trigger="cron",
                trigger_config={"hour": "1"},
            )
        ],
    )
    app_registry = AppRegistry(["fake_task_app"]).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    schedule_registry = ScheduleRegistry.from_app_registry(
        app_registry,
        task_registry=task_registry,
    )
    locks = MemoryLockProvider()
    planned_at = datetime(2026, 5, 28, 1, 0, tzinfo=UTC)
    lock_key = "scheduler:trigger:example.refresh.daily:tenant-a:2026-05-28T01:00:00+00:00"

    result = await LockedScheduleProvider(
        provider=ManualScheduleProvider(
            schedule_registry=schedule_registry,
            task_provider=SyncTaskProvider(task_registry),
        ),
        lock_provider=locks,
    ).trigger(
        ScheduleTriggerRequest(
            schedule_id="example.refresh.daily",
            tenant_id="tenant-a",
            payload={},
            request_id="req-1",
            planned_at=planned_at,
        )
    )

    assert result.ok is True
    assert result.metadata["lock_key"] == lock_key
    assert result.metadata["fencing_token"] == 1
    assert await locks.locked(lock_key) is False


def test_task_registry_rejects_duplicate_task_types(monkeypatch: pytest.MonkeyPatch) -> None:
    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        return {"task_id": envelope.task_id}

    _install_handler_module(monkeypatch, refresh=refresh)
    _install_app(
        monkeypatch,
        label="task_app",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_handlers.refresh",
            ),
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_handlers.refresh",
            ),
        ],
    )

    app_registry = AppRegistry(["fake_task_app"]).load()

    with pytest.raises(ValueError, match="Duplicate task handler"):
        TaskRegistry.from_app_registry(app_registry)


def test_schedule_registry_rejects_unknown_task_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_app(
        monkeypatch,
        label="task_app",
        schedules=[
            ScheduleSpec(
                schedule_id="example.refresh.daily",
                task_type="example.refresh",
                trigger="cron",
                trigger_config={"hour": "1"},
            )
        ],
    )

    app_registry = AppRegistry(["fake_task_app"]).load()

    with pytest.raises(ValueError, match="references unknown task"):
        ScheduleRegistry.from_app_registry(
            app_registry,
            task_registry=TaskRegistry.from_app_registry(app_registry),
        )


@pytest.mark.asyncio
async def test_sync_task_provider_enforces_tenant_lifecycle_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        return {"task_id": envelope.task_id}

    _install_handler_module(monkeypatch, refresh=refresh)
    _install_app(
        monkeypatch,
        label="task_app",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_handlers.refresh",
            )
        ],
    )
    app_registry = AppRegistry(["fake_task_app"]).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)

    with pytest.raises(AppError) as exc_info:
        await SyncTaskProvider(task_registry).submit(
            TaskEnvelope(
                task_id="task-1",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={},
                idempotency_key="example.refresh:tenant-a",
                request_id="req-1",
            ),
            tenant_status="suspended",
        )

    assert exc_info.value.code == "TENANT_STATE_FORBIDDEN"


def _install_handler_module(
    monkeypatch: pytest.MonkeyPatch,
    **handlers,
) -> None:
    handler_module = types.ModuleType("fake_task_handlers")
    for name, handler in handlers.items():
        setattr(handler_module, name, handler)
    monkeypatch.setitem(sys.modules, "fake_task_handlers", handler_module)


def _install_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    label: str,
    task_handlers: list[TaskHandlerSpec] | None = None,
    schedules: list[ScheduleSpec] | None = None,
) -> None:
    app = types.ModuleType("fake_task_app")
    app.module = AppModule(
        label=label,
        version="0.1.0",
        task_handlers=task_handlers or [],
        schedules=schedules or [],
    )
    monkeypatch.setitem(sys.modules, "fake_task_app", app)


async def _trigger_logs(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[ScheduleTriggerLog]:
    async with session_factory() as session:
        result = await session.execute(select(ScheduleTriggerLog))
        logs = list(result.scalars().all())
        for log in logs:
            session.expunge(log)
        return logs


async def _schedule_state(
    session_factory: async_sessionmaker[AsyncSession],
    schedule_id: str,
    tenant_id: str,
) -> ScheduleState | None:
    async with session_factory() as session:
        result = await session.execute(
            select(ScheduleState)
            .where(ScheduleState.schedule_id == schedule_id)
            .where(ScheduleState.tenant_id == tenant_id)
        )
        state = result.scalars().first()
        if state is not None:
            session.expunge(state)
        return state
