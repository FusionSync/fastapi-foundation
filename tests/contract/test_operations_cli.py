import asyncio
import json
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.apps import AppModule, EventHandlerSpec, ScheduleSpec, TaskHandlerSpec
from core.base.models import BaseModel
from core.cli.main import main
from core.outbox import OutboxEvent
from core.scheduler import ScheduleTriggerLog
from core.tasks import TaskRun


def test_check_config_local_profile_passes(capsys) -> None:
    exit_code = main(["check-config", "--profile", "local", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["profile"] == "local"


def test_check_config_cloud_profile_blocks_default_local_settings(capsys) -> None:
    exit_code = main(["check-config", "--profile", "cloud", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert any("JWT_SECRET" in error for error in payload["errors"])
    assert any("PostgreSQL" in error for error in payload["errors"])


def test_process_role_commands_return_health_json(capsys) -> None:
    for command in ("serve", "worker", "scheduler", "outbox-dispatcher"):
        exit_code = main([command, "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert exit_code == 0
        assert payload["ok"] is True
        assert payload["command"] == command
        assert payload["checks"]["database_configured"] is True


def test_outbox_dispatcher_role_can_run_one_iteration(tmp_path: Path, monkeypatch, capsys) -> None:
    delivered: list[str] = []
    _install_outbox_app(monkeypatch, delivered)
    database_url = _sqlite_url(tmp_path)
    event_id = asyncio.run(_seed_pending_event(database_url))

    exit_code = main(
        [
            "outbox-dispatcher",
            "--run",
            "--max-iterations",
            "1",
            "--database-url",
            database_url,
            "--installed-app",
            "fake_operations_outbox_app",
            "--dispatcher-id",
            "role-dispatcher",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {
        "ok": True,
        "command": "outbox-dispatcher",
        "role": "outbox-dispatcher",
        "dispatcher_id": "role-dispatcher",
        "iterations": 1,
        "claimed": 1,
        "published": 1,
        "failed": 0,
        "dead_lettered": 0,
    }
    assert delivered == [event_id]
    assert asyncio.run(_event_status(database_url, event_id)) == "published"


def test_scheduler_role_can_run_registered_schedule_once(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _install_scheduler_app(monkeypatch)
    database_url = _sqlite_url(tmp_path)
    asyncio.run(_create_schema(database_url))

    exit_code = main(
        [
            "scheduler",
            "--run-once",
            "--database-url",
            database_url,
            "--installed-app",
            "fake_operations_scheduler_app",
            "--schedule-id",
            "example.refresh.daily",
            "--tenant-id",
            "tenant-a",
            "--planned-at",
            "2026-05-28T01:00:00+00:00",
            "--payload-json",
            '{"value":"ok"}',
            "--request-id",
            "req-schedule",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["command"] == "scheduler"
    assert payload["role"] == "scheduler"
    assert payload["schedule_id"] == "example.refresh.daily"
    assert payload["task_type"] == "example.refresh"
    assert payload["task_result"]["status"] == "succeeded"
    assert payload["task_result"]["result_payload"] == {
        "request_id": "req-schedule",
        "tenant_id": "tenant-a",
        "value": "ok",
    }
    assert asyncio.run(_row_count(database_url, TaskRun)) == 1
    assert asyncio.run(_row_count(database_url, ScheduleTriggerLog)) == 1


def test_local_deployment_smoke_passes(capsys) -> None:
    exit_code = main(["smoke", "--profile", "local", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["checks"] == {
        "config": True,
        "server_health": True,
        "worker_health": True,
        "scheduler_health": True,
        "outbox_dispatcher_health": True,
        "migrate_health": True,
    }
    assert payload["role_health"]["server"]["checks"]["http_routes_configured"] is True
    assert payload["role_health"]["worker"]["details"]["task_provider"] == "sync"
    assert payload["role_health"]["outbox-dispatcher"]["checks"][
        "outbox_claim_loop_configured"
    ] is True


def test_backup_check_requires_timestamp_for_private_profile(capsys) -> None:
    exit_code = main(["backup-check", "--profile", "private", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert "latest_backup_at is required" in payload["errors"][0]


def test_backup_check_accepts_recent_backup(capsys) -> None:
    latest_backup_at = datetime.now(UTC).isoformat()

    exit_code = main(
        [
            "backup-check",
            "--profile",
            "private",
            "--latest-backup-at",
            latest_backup_at,
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True


def _sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'operations-outbox.db'}"


async def _seed_pending_event(database_url: str) -> str:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            event = OutboxEvent(
                tenant_id="tenant-a",
                event_type="business.created",
                event_version=1,
                aggregate_type="business_record",
                aggregate_id="record-1",
                payload={
                    "tenant_id": "tenant-a",
                    "actor_id": "user-1",
                    "request_id": "req_test",
                },
                status="pending",
            )
            session.add(event)
            await session.commit()
            return event.id
    finally:
        await engine.dispose()


async def _create_schema(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(BaseModel.metadata.create_all)
    finally:
        await engine.dispose()


async def _event_status(database_url: str, event_id: str) -> str:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            event = await session.get(OutboxEvent, event_id)
            assert event is not None
            return event.status
    finally:
        await engine.dispose()


async def _row_count(database_url: str, model) -> int:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            from sqlalchemy import func, select

            result = await session.scalar(select(func.count()).select_from(model))
            return int(result or 0)
    finally:
        await engine.dispose()


def _install_outbox_app(monkeypatch, delivered: list[str]) -> None:
    handler_module = types.ModuleType("fake_operations_outbox_handlers")

    def handle_business_created(envelope) -> None:
        delivered.append(envelope.event_id)

    handler_module.handle_business_created = handle_business_created
    monkeypatch.setitem(sys.modules, "fake_operations_outbox_handlers", handler_module)

    app_module = types.ModuleType("fake_operations_outbox_app")
    app_module.module = AppModule(
        label="operations_outbox_app",
        version="0.1.0",
        event_handlers=[
            EventHandlerSpec(
                event_type="business.created",
                event_version=1,
                handler_path="fake_operations_outbox_handlers.handle_business_created",
            )
        ],
    )
    monkeypatch.setitem(sys.modules, "fake_operations_outbox_app", app_module)


def _install_scheduler_app(monkeypatch) -> None:
    handler_module = types.ModuleType("fake_operations_scheduler_handlers")

    def refresh(envelope) -> dict[str, str]:
        return {
            "request_id": envelope.request_id,
            "tenant_id": envelope.tenant_id,
            "value": envelope.payload["value"],
        }

    handler_module.refresh = refresh
    monkeypatch.setitem(sys.modules, "fake_operations_scheduler_handlers", handler_module)

    app_module = types.ModuleType("fake_operations_scheduler_app")
    app_module.module = AppModule(
        label="operations_scheduler_app",
        version="0.1.0",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_operations_scheduler_handlers.refresh",
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
    monkeypatch.setitem(sys.modules, "fake_operations_scheduler_app", app_module)
