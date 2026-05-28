import asyncio
import json
import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.apps import AppModule, TaskHandlerSpec
from core.base.models import BaseModel
from core.cli.main import main
from core.tasks import TaskRun


def test_tasks_failed_list_outputs_stable_json(tmp_path: Path, capsys) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"
    asyncio.run(_seed_failed_task(database_url))

    exit_code = main(["tasks", "failed", "list", "--database-url", database_url, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["tasks"][0]["task_id"] == "task-failed"
    assert payload["tasks"][0]["status"] == "failed"
    assert payload["tasks"][0]["attempt_count"] == 1
    assert payload["tasks"][0]["max_attempts"] == 2


def test_tasks_failed_retry_requires_yes(tmp_path: Path, capsys) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"
    asyncio.run(_seed_failed_task(database_url))

    exit_code = main(
        [
            "tasks",
            "failed",
            "retry",
            "--database-url",
            database_url,
            "--task-id",
            "task-failed",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {"ok": False, "error": "tasks failed retry requires --yes"}
    assert asyncio.run(_task_status(database_url, "task-failed")) == "failed"


def test_tasks_failed_retry_runs_registered_handler(tmp_path: Path, capsys) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"
    asyncio.run(_seed_failed_task(database_url))
    _install_task_app()

    exit_code = main(
        [
            "tasks",
            "failed",
            "retry",
            "--database-url",
            database_url,
            "--installed-app",
            "fake_task_cli_app",
            "--task-id",
            "task-failed",
            "--yes",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["task"]["status"] == "succeeded"
    assert payload["task"]["attempt_count"] == 2
    assert payload["result"]["result_payload"] == {"retried": "task-failed"}


def test_tasks_running_recover_requires_yes(tmp_path: Path, capsys) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"
    asyncio.run(_seed_running_task(database_url))

    exit_code = main(
        [
            "tasks",
            "running",
            "recover",
            "--database-url",
            database_url,
            "--older-than-seconds",
            "60",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {"ok": False, "error": "tasks running recover requires --yes"}
    assert asyncio.run(_task_status(database_url, "task-running")) == "running"


def test_tasks_running_recover_marks_stale_tasks_failed(tmp_path: Path, capsys) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"
    asyncio.run(_seed_running_task(database_url))

    exit_code = main(
        [
            "tasks",
            "running",
            "recover",
            "--database-url",
            database_url,
            "--older-than-seconds",
            "60",
            "--yes",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["tasks"][0]["task_id"] == "task-running"
    assert payload["tasks"][0]["status"] == "failed"
    assert payload["tasks"][0]["error_message"] == "Task run recovered after worker interruption"
    assert asyncio.run(_task_status(database_url, "task-running")) == "failed"


async def _seed_failed_task(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                TaskRun(
                    id="task-failed",
                    tenant_id="tenant-a",
                    task_type="example.refresh",
                    idempotency_key="example.refresh:tenant-a:failed",
                    status="failed",
                    progress=0,
                    input_payload={"value": "retry"},
                    result_payload=None,
                    error_message="RuntimeError: failed",
                    queue="default",
                    attempt_count=1,
                    max_attempts=2,
                    request_id="req-task",
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _seed_running_task(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                TaskRun(
                    id="task-running",
                    tenant_id="tenant-a",
                    task_type="example.refresh",
                    idempotency_key="example.refresh:tenant-a:running",
                    status="running",
                    progress=0,
                    input_payload={"value": "recover"},
                    result_payload=None,
                    error_message=None,
                    queue="default",
                    attempt_count=1,
                    max_attempts=2,
                    request_id="req-task-running",
                    started_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _task_status(database_url: str, task_id: str) -> str:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            task_run = await session.get(TaskRun, task_id)
            assert task_run is not None
            return task_run.status
    finally:
        await engine.dispose()


def _install_task_app() -> None:
    handler_module = types.ModuleType("fake_task_cli_handlers")

    def refresh(envelope: Any) -> dict[str, str]:
        return {"retried": envelope.task_id}

    handler_module.refresh = refresh
    app_module = types.ModuleType("fake_task_cli_app")
    app_module.module = AppModule(
        label="task_cli_app",
        version="0.1.0",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_cli_handlers.refresh",
            )
        ],
    )
    sys.modules["fake_task_cli_handlers"] = handler_module
    sys.modules["fake_task_cli_app"] = app_module
