import sys
import types
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.apps import AppModule, AppRegistry, TaskHandlerSpec
from core.base.models import BaseModel
from core.context import (
    RequestContext,
    get_current_context,
    reset_current_context,
    set_current_context,
)
from core.db import unit_of_work
from core.exceptions import AppError
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
async def test_sync_task_provider_sets_background_context_for_handler_and_resets(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seen_contexts: list[RequestContext | None] = []

    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        seen_contexts.append(get_current_context())
        return {"task_id": envelope.task_id}

    task_registry = _task_registry(monkeypatch, refresh=refresh)
    outer_context = RequestContext(
        request_id="req-outer",
        tenant_id="tenant-outer",
    ).freeze()
    token = set_current_context(outer_context)
    try:
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            await SyncTaskProvider(
                task_registry,
                task_repository=TaskRunRepository(uow.session),
            ).submit(
                TaskEnvelope(
                    task_id="task-context-1",
                    task_type="example.refresh",
                    tenant_id="tenant-a",
                    payload={},
                    idempotency_key="example.refresh:tenant-a:context",
                    request_id="req-task",
                    trace_id="trace-task",
                )
            )

        assert get_current_context() == outer_context
    finally:
        reset_current_context(token)

    assert len(seen_contexts) == 1
    context = seen_contexts[0]
    assert context is not None
    assert context.request_id == "req-task"
    assert context.trace_id == "trace-task"
    assert context.tenant_id == "tenant-a"
    assert context.route == "task:example.refresh"
    assert context.method == "TASK"
    assert context.frozen is True


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


@pytest.mark.asyncio
async def test_sync_task_provider_retries_failed_run_and_dead_letters_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        raise RuntimeError(f"still failing {envelope.task_id}")

    task_registry = _task_registry(monkeypatch, refresh=refresh)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        provider = SyncTaskProvider(
            task_registry,
            task_repository=TaskRunRepository(uow.session),
            max_attempts=2,
        )
        first_result = await provider.submit(
            TaskEnvelope(
                task_id="task-3",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={"value": "bad"},
                idempotency_key="example.refresh:tenant-a:retry",
                request_id="req-3",
            )
        )

    first_run = await _task_run(session_factory, "task-3")
    assert first_result.status == "failed"
    assert first_run.status == "failed"
    assert first_run.attempt_count == 1
    assert first_run.max_attempts == 2

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        retry_run = await uow.session.get(TaskRun, "task-3")
        assert retry_run is not None
        retry_result = await SyncTaskProvider(
            task_registry,
            task_repository=TaskRunRepository(uow.session),
        ).retry(retry_run)

    retried_run = await _task_run(session_factory, "task-3")
    assert retry_result.status == "dead_letter"
    assert retried_run.status == "dead_letter"
    assert retried_run.attempt_count == 2
    assert retried_run.error_message == "RuntimeError: still failing task-3"


@pytest.mark.asyncio
async def test_sync_task_provider_replays_duplicate_idempotency_key_without_reexecution(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    calls: list[str] = []

    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        calls.append(envelope.task_id)
        return {"value": str(envelope.payload["value"])}

    task_registry = _task_registry(monkeypatch, refresh=refresh)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        first_result = await SyncTaskProvider(
            task_registry,
            task_repository=TaskRunRepository(uow.session),
        ).submit(
            TaskEnvelope(
                task_id="task-duplicate-1",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={"value": "ok"},
                idempotency_key="example.refresh:tenant-a:duplicate",
                request_id="req-1",
            )
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        replayed_result = await SyncTaskProvider(
            task_registry,
            task_repository=TaskRunRepository(uow.session),
        ).submit(
            TaskEnvelope(
                task_id="task-duplicate-2",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={"value": "ok"},
                idempotency_key="example.refresh:tenant-a:duplicate",
                request_id="req-2",
            )
        )

    assert first_result.ok is True
    assert replayed_result.ok is True
    assert replayed_result.task_id == "task-duplicate-1"
    assert replayed_result.result_payload == {"value": "ok"}
    assert replayed_result.metadata["idempotency"] == "replayed"
    assert calls == ["task-duplicate-1"]
    assert await _task_run_count(session_factory) == 1


@pytest.mark.asyncio
async def test_sync_task_provider_reports_duplicate_pending_task_without_reexecution(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    calls: list[str] = []

    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        calls.append(envelope.task_id)
        return {"value": str(envelope.payload["value"])}

    task_registry = _task_registry(monkeypatch, refresh=refresh)
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(
            TaskRun(
                id="task-pending-existing",
                tenant_id="tenant-a",
                task_type="example.refresh",
                idempotency_key="example.refresh:tenant-a:pending",
                status="pending",
                progress=0,
                input_payload={"value": "queued"},
                queue="default",
                attempt_count=0,
                max_attempts=3,
                request_id="req-pending",
            )
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        result = await SyncTaskProvider(
            task_registry,
            task_repository=TaskRunRepository(uow.session),
        ).submit(
            TaskEnvelope(
                task_id="task-pending-duplicate",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={"value": "queued"},
                idempotency_key="example.refresh:tenant-a:pending",
                request_id="req-duplicate",
            )
        )

    assert result.status == "pending"
    assert result.task_id == "task-pending-existing"
    assert result.metadata["idempotency"] == "in_progress"
    assert calls == []
    assert await _task_run_count(session_factory) == 1


@pytest.mark.asyncio
async def test_sync_task_provider_rejects_idempotency_key_payload_conflict(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        return {"value": str(envelope.payload["value"])}

    task_registry = _task_registry(monkeypatch, refresh=refresh)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await SyncTaskProvider(
            task_registry,
            task_repository=TaskRunRepository(uow.session),
        ).submit(
            TaskEnvelope(
                task_id="task-conflict-1",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={"value": "first"},
                idempotency_key="example.refresh:tenant-a:conflict",
                request_id="req-1",
            )
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as exc_info:
            await SyncTaskProvider(
                task_registry,
                task_repository=TaskRunRepository(uow.session),
            ).submit(
                TaskEnvelope(
                    task_id="task-conflict-2",
                    task_type="example.refresh",
                    tenant_id="tenant-a",
                    payload={"value": "second"},
                    idempotency_key="example.refresh:tenant-a:conflict",
                    request_id="req-2",
                )
            )

    assert exc_info.value.code == "TASK_IDEMPOTENCY_KEY_CONFLICT"
    assert await _task_run_count(session_factory) == 1


@pytest.mark.asyncio
async def test_task_repository_recovers_stale_running_tasks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    stale_started_at = datetime(2026, 1, 1, tzinfo=UTC)
    fresh_started_at = datetime(2026, 1, 2, tzinfo=UTC)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add_all(
            [
                _running_task(
                    "task-stale-retryable",
                    started_at=stale_started_at,
                    attempt_count=1,
                    max_attempts=2,
                ),
                _running_task(
                    "task-stale-dead-letter",
                    started_at=stale_started_at,
                    attempt_count=2,
                    max_attempts=2,
                ),
                _running_task(
                    "task-fresh",
                    started_at=fresh_started_at,
                    attempt_count=1,
                    max_attempts=2,
                ),
            ]
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        recovered = await TaskRunRepository(uow.session).recover_stale_running(
            older_than=fresh_started_at - timedelta(seconds=1),
            now=fresh_started_at,
        )

    retryable = await _task_run(session_factory, "task-stale-retryable")
    dead_letter = await _task_run(session_factory, "task-stale-dead-letter")
    fresh = await _task_run(session_factory, "task-fresh")

    assert [task_run.id for task_run in recovered] == [
        "task-stale-retryable",
        "task-stale-dead-letter",
    ]
    assert retryable.status == "failed"
    assert retryable.error_message == "Task run recovered after worker interruption"
    assert retryable.finished_at == fresh_started_at.replace(tzinfo=None)
    assert dead_letter.status == "dead_letter"
    assert fresh.status == "running"
    assert fresh.error_message is None


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


async def _task_run_count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        result = await session.execute(select(TaskRun))
        return len(result.scalars().all())


def _running_task(
    task_id: str,
    *,
    started_at: datetime,
    attempt_count: int,
    max_attempts: int,
) -> TaskRun:
    return TaskRun(
        id=task_id,
        tenant_id="tenant-a",
        task_type="example.refresh",
        idempotency_key=f"example.refresh:tenant-a:{task_id}",
        status="running",
        progress=0,
        input_payload={"value": task_id},
        result_payload=None,
        error_message=None,
        queue="default",
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        request_id=f"req-{task_id}",
        started_at=started_at,
    )
