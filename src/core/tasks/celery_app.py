from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.apps import AppRegistry, resolve_runtime_capabilities
from core.config import Settings, get_settings
from core.db import unit_of_work
from core.tasks.provider import SyncTaskProvider, TaskResult
from core.tasks.registry import TaskRegistry
from core.tasks.repository import TaskRunRepository


def create_celery_app(settings: Settings | None = None) -> Any:
    try:
        from celery import Celery
    except ImportError as exc:
        raise RuntimeError(
            "Celery task provider requires the celery package. Install project dependencies again."
        ) from exc

    resolved_settings = settings or get_settings()
    broker_url = resolved_settings.dependencies.rabbitmq_url
    if not broker_url:
        raise RuntimeError("Celery task provider requires DEPENDENCIES__RABBITMQ_URL")

    app = Celery("fastapi_foundation", broker=broker_url)
    if resolved_settings.dependencies.redis_url:
        app.conf.result_backend = resolved_settings.dependencies.redis_url
    app.conf.update(
        task_default_queue="default",
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
    )

    @app.task(name="core.tasks.execute")
    def execute_task(task_id: str) -> dict[str, object]:
        return asyncio.run(run_persisted_task(task_id)).to_dict()

    return app


async def run_persisted_task(task_id: str, settings: Settings | None = None) -> TaskResult:
    resolved_settings = settings or get_settings()
    engine = create_async_engine(resolved_settings.database.url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app_registry = AppRegistry(
        resolved_settings.installed_apps,
        runtime_capabilities=resolve_runtime_capabilities(
            resolved_settings,
            database_url=resolved_settings.database.url,
            service_role="worker",
        ),
    ).load()
    task_registry = TaskRegistry.from_app_registry(app_registry)
    try:
        async with unit_of_work(session_factory) as uow:
            if uow.session is None:
                raise RuntimeError("database session was not initialized")
            repository = TaskRunRepository(uow.session)
            task_run = await repository.require(task_id)
            return await SyncTaskProvider(
                task_registry,
                task_repository=repository,
                max_attempts=resolved_settings.task_queue.max_attempts,
            ).run_task_run(task_run)
    finally:
        await engine.dispose()
