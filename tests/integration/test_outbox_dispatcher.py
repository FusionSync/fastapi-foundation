from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.db import unit_of_work
from core.events import EventEnvelope, EventRegistry
from core.outbox import OutboxDispatcher, OutboxEvent, OutboxRepository


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
async def test_dispatcher_publishes_claimed_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    delivered: list[str] = []
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: delivered.append(event.event_id))
    await _add_event(session_factory, registry)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        repository = OutboxRepository(uow.session, registry=registry)
        dispatcher = OutboxDispatcher(repository, registry, dispatcher_id="dispatcher-1")
        stats = await dispatcher.dispatch_once()

    event = await _first_event(session_factory)
    assert stats.claimed == 1
    assert stats.published == 1
    assert delivered == [event.id]
    assert event.status == "published"
    assert event.published_at is not None


@pytest.mark.asyncio
async def test_dispatcher_retries_failed_event_then_publishes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    calls: list[str] = []
    registry = EventRegistry()

    def flaky_handler(event: EventEnvelope) -> None:
        calls.append(event.event_id)
        if len(calls) == 1:
            raise RuntimeError("temporary failure")

    registry.register("business.created", 1, flaky_handler)
    await _add_event(session_factory, registry, max_attempts=2)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        dispatcher = OutboxDispatcher(
            OutboxRepository(uow.session, registry=registry),
            registry,
            dispatcher_id="dispatcher-1",
            retry_delay_seconds=0,
        )
        first_stats = await dispatcher.dispatch_once()

    first_event = await _first_event(session_factory)
    assert first_stats.failed == 1
    assert first_event.status == "failed"
    assert first_event.attempt_count == 1

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        dispatcher = OutboxDispatcher(
            OutboxRepository(uow.session, registry=registry),
            registry,
            dispatcher_id="dispatcher-1",
            retry_delay_seconds=0,
        )
        second_stats = await dispatcher.dispatch_once()

    second_event = await _first_event(session_factory)
    assert second_stats.published == 1
    assert second_event.status == "published"
    assert calls == [second_event.id, second_event.id]


@pytest.mark.asyncio
async def test_dispatcher_moves_exhausted_event_to_dead_letter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = EventRegistry()
    registry.register(
        "business.created",
        1,
        lambda event: (_ for _ in ()).throw(RuntimeError("permanent failure")),
    )
    await _add_event(session_factory, registry, max_attempts=1)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        dispatcher = OutboxDispatcher(
            OutboxRepository(uow.session, registry=registry),
            registry,
            dispatcher_id="dispatcher-1",
            retry_delay_seconds=0,
        )
        stats = await dispatcher.dispatch_once()

    event = await _first_event(session_factory)
    assert stats.failed == 1
    assert stats.dead_lettered == 1
    assert event.status == "dead_letter"
    assert event.dead_letter_reason is not None


@pytest.mark.asyncio
async def test_dead_letter_event_can_be_replayed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: None)
    await _add_event(session_factory, registry, max_attempts=1)
    event = await _first_event(session_factory)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        event = await uow.session.get(OutboxEvent, event.id)
        assert event is not None
        event.status = "dead_letter"
        event.dead_letter_reason = "manual test"
        await OutboxRepository(uow.session, registry=registry).replay_dead_letter(event)

    replayed = await _first_event(session_factory)
    assert replayed.status == "pending"
    assert replayed.dead_letter_reason is None


async def _add_event(
    session_factory: async_sessionmaker[AsyncSession],
    registry: EventRegistry,
    *,
    max_attempts: int = 3,
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await OutboxRepository(uow.session, registry=registry).add(
            event_type="business.created",
            aggregate_type="business_record",
            aggregate_id="record-1",
            tenant_id="tenant-a",
            payload=_payload(),
            max_attempts=max_attempts,
        )


async def _first_event(session_factory: async_sessionmaker[AsyncSession]) -> OutboxEvent:
    async with session_factory() as session:
        result = await session.execute(select(OutboxEvent).limit(1))
        event = result.scalars().one()
        session.expunge(event)
        return event


def _payload(**overrides: Any) -> dict[str, Any]:
    return {
        "tenant_id": "tenant-a",
        "actor_id": "user-1",
        "request_id": "req_test",
        **overrides,
    }
