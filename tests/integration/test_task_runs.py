import sys
import types
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.apps import AppModule, AppRegistry, TaskHandlerSpec
from core.base.models import BaseModel
from core.db import unit_of_work
from core.tasks import SyncTaskProvider, TaskEnvelope, TaskRegistry, TaskRun, TaskRunRepository


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
async def test_sync_task_provider_persists_successful_task_run(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        return {"value": str(envelope.payload["value"])}

    task_registry = _task_registry(monkeypatch, refresh=refresh)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        result = await SyncTaskProvider(
            task_registry,
            task_repository=TaskRunRepository(uow.session),
        ).submit(
            TaskEnvelope(
                task_id="task-1",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={"value": "ok"},
                idempotency_key="example.refresh:tenant-a",
                request_id="req-1",
            )
        )

    task_run = await _task_run(session_factory, "task-1")
    assert result.ok is True
    assert task_run.status == "succeeded"
    assert task_run.task_type == "example.refresh"
    assert task_run.tenant_id == "tenant-a"
    assert task_run.input_payload == {"value": "ok"}
    assert task_run.result_payload == {"value": "ok"}
    assert task_run.queue == "default"
    assert task_run.request_id == "req-1"
    assert task_run.finished_at is not None


@pytest.mark.asyncio
async def test_sync_task_provider_persists_failed_task_run(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        raise RuntimeError(f"cannot refresh {envelope.task_id}")

    task_registry = _task_registry(monkeypatch, refresh=refresh)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        result = await SyncTaskProvider(
            task_registry,
            task_repository=TaskRunRepository(uow.session),
        ).submit(
            TaskEnvelope(
                task_id="task-2",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={"value": "bad"},
                idempotency_key="example.refresh:tenant-a:bad",
                request_id="req-2",
            )
        )

    task_run = await _task_run(session_factory, "task-2")
    assert result.ok is False
    assert task_run.status == "failed"
    assert task_run.error_message == "RuntimeError: cannot refresh task-2"
    assert task_run.result_payload is None
    assert task_run.finished_at is not None


def _task_registry(
    monkeypatch: pytest.MonkeyPatch,
    **handlers,
) -> TaskRegistry:
    handler_module = types.ModuleType("fake_task_run_handlers")
    for name, handler in handlers.items():
        setattr(handler_module, name, handler)
    app_module = types.ModuleType("fake_task_run_app")
    app_module.module = AppModule(
        label="task_run_app",
        version="0.1.0",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_task_run_handlers.refresh",
            )
        ],
    )
    monkeypatch.setitem(sys.modules, "fake_task_run_handlers", handler_module)
    monkeypatch.setitem(sys.modules, "fake_task_run_app", app_module)
    return TaskRegistry.from_app_registry(AppRegistry(["fake_task_run_app"]).load())


async def _task_run(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: str,
) -> TaskRun:
    async with session_factory() as session:
        result = await session.execute(select(TaskRun).where(TaskRun.id == task_id))
        return result.scalars().one()
