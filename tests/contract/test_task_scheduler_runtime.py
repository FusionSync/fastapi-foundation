import sys
import types
from datetime import UTC, datetime

import pytest

from core.apps import AppModule, AppRegistry, ScheduleSpec, TaskHandlerSpec
from core.exceptions import AppError
from core.scheduler import ManualScheduleProvider, ScheduleRegistry, ScheduleTriggerRequest
from core.tasks import SyncTaskProvider, TaskEnvelope, TaskRegistry


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
