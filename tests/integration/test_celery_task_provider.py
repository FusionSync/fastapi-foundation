import sys
import types
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.apps import AppModule, AppRegistry, TaskHandlerSpec
from core.base.models import Model
from core.db import unit_of_work
from core.tasks import CeleryTaskProvider, TaskEnvelope, TaskRegistry, TaskRunRepository


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Model.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_celery_task_provider_persists_task_run_and_enqueues_celery_message(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    celery_app = FakeCeleryApp()
    task_registry = _task_registry()

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        result = await CeleryTaskProvider(
            task_registry,
            task_repository=TaskRunRepository(uow.session),
            celery_app=celery_app,
            celery_task_name="core.tasks.execute",
        ).submit(
            TaskEnvelope(
                task_id="task-celery-1",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={"value": "queued"},
                idempotency_key="example.refresh:tenant-a:celery",
                request_id="req-celery",
                trace_id="trace-celery",
            )
        )

    assert result.ok is True
    assert result.status == "pending"
    assert result.metadata == {
        "provider": "celery",
        "queue": "default",
        "idempotency": "started",
        "celery_task_name": "core.tasks.execute",
    }
    assert celery_app.sent_tasks == [
        {
            "name": "core.tasks.execute",
            "kwargs": {"task_id": "task-celery-1"},
            "queue": "default",
            "task_id": "task-celery-1",
        }
    ]


@pytest.mark.asyncio
async def test_celery_task_provider_does_not_enqueue_duplicate_in_progress_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    celery_app = FakeCeleryApp()
    task_registry = _task_registry()
    envelope = TaskEnvelope(
        task_id="task-celery-duplicate-1",
        task_type="example.refresh",
        tenant_id="tenant-a",
        payload={"value": "queued"},
        idempotency_key="example.refresh:tenant-a:celery-duplicate",
        request_id="req-celery",
    )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        provider = CeleryTaskProvider(
            task_registry,
            task_repository=TaskRunRepository(uow.session),
            celery_app=celery_app,
        )
        first = await provider.submit(envelope)
        duplicate = await provider.submit(
            TaskEnvelope(
                task_id="task-celery-duplicate-2",
                task_type=envelope.task_type,
                tenant_id=envelope.tenant_id,
                payload=envelope.payload,
                idempotency_key=envelope.idempotency_key,
                request_id="req-celery-duplicate",
            )
        )

    assert first.metadata["idempotency"] == "started"
    assert duplicate.task_id == "task-celery-duplicate-1"
    assert duplicate.metadata["idempotency"] == "in_progress"
    assert len(celery_app.sent_tasks) == 1


class FakeCeleryApp:
    def __init__(self) -> None:
        self.sent_tasks: list[dict[str, object]] = []

    def send_task(
        self,
        name: str,
        *,
        kwargs: dict[str, object],
        queue: str,
        task_id: str,
    ) -> None:
        self.sent_tasks.append(
            {
                "name": name,
                "kwargs": kwargs,
                "queue": queue,
                "task_id": task_id,
            }
        )


def _task_registry() -> TaskRegistry:
    handler_module = types.ModuleType("fake_celery_task_handlers")

    def refresh(envelope):
        return {"value": envelope.payload["value"]}

    handler_module.refresh = refresh
    app_module = types.ModuleType("fake_celery_task_app")
    app_module.module = AppModule(
        label="celery_task_app",
        version="0.1.0",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_celery_task_handlers.refresh",
            )
        ],
    )
    sys.modules["fake_celery_task_handlers"] = handler_module
    sys.modules["fake_celery_task_app"] = app_module
    return TaskRegistry.from_app_registry(AppRegistry(["fake_celery_task_app"]).load())
