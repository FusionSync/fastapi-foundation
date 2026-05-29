from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.apps import AppRegistry, resolve_runtime_capabilities
from core.config import get_settings
from core.db import unit_of_work
from core.operations import ProcessHeartbeatRepository
from core.tasks.provider import DatabaseQueueTaskProvider, SyncTaskProvider
from core.tasks.registry import TaskRegistry
from core.tasks.repository import TaskRunRepository


@dataclass(frozen=True, slots=True)
class TaskWorkerRunResult:
    ok: bool
    queue: str
    iterations: int
    instance_id: str | None = None
    claimed: int = 0
    succeeded: int = 0
    failed: int = 0
    dead_lettered: int = 0

    def to_dict(self, *, include_iterations: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "queue": self.queue,
            "claimed": self.claimed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "dead_lettered": self.dead_lettered,
        }
        if include_iterations:
            payload["iterations"] = self.iterations
        if self.instance_id is not None:
            payload["instance_id"] = self.instance_id
        return payload


async def run_task_worker_loop(
    *,
    database_url: str,
    module_paths: list[str],
    queue: str,
    tenant_status: str,
    provider: str = "sync",
    max_attempts: int = 3,
    retry_backoff_seconds: int = 30,
    instance_id: str | None = None,
    max_iterations: int | None = None,
    idle_sleep_seconds: float = 1.0,
) -> TaskWorkerRunResult:
    task_registry = TaskRegistry.from_app_registry(
        AppRegistry(
            module_paths,
            runtime_capabilities=resolve_runtime_capabilities(
                get_settings(),
                database_url=database_url,
                service_role="worker",
            ),
        ).load()
    )
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    iterations = 0
    claimed = 0
    succeeded = 0
    failed = 0
    dead_lettered = 0
    try:
        while max_iterations is None or iterations < max_iterations:
            async with unit_of_work(session_factory) as uow:
                if uow.session is None:
                    raise RuntimeError("database session was not initialized")
                repository = TaskRunRepository(uow.session)
                if provider == "database":
                    result = await DatabaseQueueTaskProvider(
                        task_registry,
                        task_repository=repository,
                        max_attempts=max_attempts,
                        retry_backoff_seconds=retry_backoff_seconds,
                    ).run_next(
                        queue=queue,
                        tenant_status=tenant_status,  # type: ignore[arg-type]
                    )
                else:
                    task_run = await repository.claim_next_pending(queue=queue)
                    result = None
                    if task_run is not None:
                        result = await SyncTaskProvider(
                            task_registry,
                            task_repository=repository,
                            max_attempts=max_attempts,
                        ).run_task_run(
                            task_run,
                            tenant_status=tenant_status,  # type: ignore[arg-type]
                        )
                iterations += 1
                if result is not None:
                    claimed += 1
                    if result.status == "succeeded":
                        succeeded += 1
                    else:
                        failed += 1
                        if result.status == "dead_letter":
                            dead_lettered += 1
                if instance_id is not None:
                    await ProcessHeartbeatRepository(uow.session).record(
                        role="worker",
                        instance_id=instance_id,
                        details={
                            "queue": queue,
                            "iterations": iterations,
                            "claimed": claimed,
                            "succeeded": succeeded,
                            "failed": failed,
                            "dead_lettered": dead_lettered,
                        },
                    )
            if result is None and max_iterations is None:
                await asyncio.sleep(idle_sleep_seconds)
    finally:
        await engine.dispose()

    return TaskWorkerRunResult(
        ok=failed == 0 and dead_lettered == 0,
        queue=queue,
        iterations=iterations,
        instance_id=instance_id,
        claimed=claimed,
        succeeded=succeeded,
        failed=failed,
        dead_lettered=dead_lettered,
    )
