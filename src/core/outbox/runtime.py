from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.apps import AppRegistry, resolve_runtime_capabilities
from core.config import get_settings
from core.db import unit_of_work
from core.events import EventRegistry
from core.operations import ProcessHeartbeatRepository
from core.outbox.dispatcher import OutboxDispatcher
from core.outbox.repository import OutboxRepository


@dataclass(frozen=True, slots=True)
class OutboxDispatchRunResult:
    ok: bool
    dispatcher_id: str
    iterations: int
    instance_id: str | None = None
    claimed: int = 0
    published: int = 0
    failed: int = 0
    dead_lettered: int = 0

    def to_dict(self, *, include_iterations: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "dispatcher_id": self.dispatcher_id,
            "claimed": self.claimed,
            "published": self.published,
            "failed": self.failed,
            "dead_lettered": self.dead_lettered,
        }
        if include_iterations:
            payload["iterations"] = self.iterations
        if self.instance_id is not None:
            payload["instance_id"] = self.instance_id
        return payload


async def run_outbox_dispatch_once(
    *,
    database_url: str,
    module_paths: list[str],
    dispatcher_id: str,
    batch_size: int,
    instance_id: str | None = None,
) -> OutboxDispatchRunResult:
    return await run_outbox_dispatch_loop(
        database_url=database_url,
        module_paths=module_paths,
        dispatcher_id=dispatcher_id,
        batch_size=batch_size,
        instance_id=instance_id,
        max_iterations=1,
        idle_sleep_seconds=0,
    )


async def run_outbox_dispatch_loop(
    *,
    database_url: str,
    module_paths: list[str],
    dispatcher_id: str,
    batch_size: int,
    instance_id: str | None = None,
    max_iterations: int | None = None,
    idle_sleep_seconds: float = 1.0,
) -> OutboxDispatchRunResult:
    registry = EventRegistry.from_app_registry(
        AppRegistry(
            module_paths,
            runtime_capabilities=resolve_runtime_capabilities(
                get_settings(),
                database_url=database_url,
                service_role="outbox-dispatcher",
            ),
        ).load()
    )
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    iterations = 0
    claimed = 0
    published = 0
    failed = 0
    dead_lettered = 0
    try:
        while max_iterations is None or iterations < max_iterations:
            async with unit_of_work(session_factory) as uow:
                if uow.session is None:
                    raise RuntimeError("database session was not initialized")
                stats = await OutboxDispatcher(
                    OutboxRepository(uow.session, registry=registry),
                    registry,
                    dispatcher_id=dispatcher_id,
                    batch_size=batch_size,
                ).dispatch_once()
                iterations += 1
                claimed += stats.claimed
                published += stats.published
                failed += stats.failed
                dead_lettered += stats.dead_lettered
                if instance_id is not None:
                    await ProcessHeartbeatRepository(uow.session).record(
                        role="outbox-dispatcher",
                        instance_id=instance_id,
                        details={
                            "dispatcher_id": dispatcher_id,
                            "iterations": iterations,
                            "claimed": claimed,
                            "published": published,
                            "failed": failed,
                            "dead_lettered": dead_lettered,
                        },
                    )
            if max_iterations is None and stats.claimed == 0:
                await asyncio.sleep(idle_sleep_seconds)
    finally:
        await engine.dispose()

    return OutboxDispatchRunResult(
        ok=failed == 0 and dead_lettered == 0,
        dispatcher_id=dispatcher_id,
        iterations=iterations,
        instance_id=instance_id,
        claimed=claimed,
        published=published,
        failed=failed,
        dead_lettered=dead_lettered,
    )
