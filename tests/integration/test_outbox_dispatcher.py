from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.apps import EventHandlerSpec, EventSchemaSpec
from core.base.models import BaseModel
from core.context import (
    RequestContext,
    get_current_context,
    reset_current_context,
    set_current_context,
)
from core.db import unit_of_work
from core.events import EventEnvelope, EventHandlerPermanentError, EventRegistry
from core.exceptions import AppError
from core.idempotency import IdempotencyStore, hash_request_payload
from core.locks import MemoryLockProvider
from core.observability import MetricsRegistry
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
    metrics = MetricsRegistry()
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: delivered.append(event.event_id))
    await _add_event(session_factory, registry)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        repository = OutboxRepository(uow.session, registry=registry)
        dispatcher = OutboxDispatcher(
            repository,
            registry,
            dispatcher_id="dispatcher-1",
            metrics=metrics,
        )
        stats = await dispatcher.dispatch_once()

    event = await _first_event(session_factory)
    rendered_metrics = metrics.render()
    assert stats.claimed == 1
    assert stats.published == 1
    assert delivered == [event.id]
    assert event.status == "published"
    assert event.published_at is not None
    assert 'outbox_dispatch_events_total{outcome="claimed"} 1' in rendered_metrics
    assert 'outbox_dispatch_events_total{outcome="published"} 1' in rendered_metrics


@pytest.mark.asyncio
async def test_dispatcher_skips_claim_when_cross_process_lock_is_held(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    delivered: list[str] = []
    locks = MemoryLockProvider()
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: delivered.append(event.event_id))
    await _add_event(session_factory, registry)
    await locks.acquire("outbox:dispatch", owner_token="dispatcher-2", ttl_seconds=60)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        stats = await OutboxDispatcher(
            OutboxRepository(uow.session, registry=registry),
            registry,
            dispatcher_id="dispatcher-1",
            lock_provider=locks,
            lock_key="outbox:dispatch",
            lock_ttl_seconds=60,
        ).dispatch_once()

    event = await _first_event(session_factory)
    assert stats.claimed == 0
    assert stats.published == 0
    assert stats.failed == 0
    assert stats.dead_lettered == 0
    assert delivered == []
    assert event.status == "pending"
    assert event.locked_by is None


@pytest.mark.asyncio
async def test_dispatcher_releases_cross_process_lock_after_dispatch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    locks = MemoryLockProvider()
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: None)
    await _add_event(session_factory, registry)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        stats = await OutboxDispatcher(
            OutboxRepository(uow.session, registry=registry),
            registry,
            dispatcher_id="dispatcher-1",
            lock_provider=locks,
            lock_key="outbox:dispatch",
            lock_ttl_seconds=60,
        ).dispatch_once()

    assert stats.published == 1
    assert await locks.locked("outbox:dispatch") is False


@pytest.mark.asyncio
async def test_dispatcher_sets_background_context_for_handler_and_resets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seen_contexts: list[RequestContext | None] = []
    registry = EventRegistry()
    registry.register(
        "business.created",
        1,
        lambda event: seen_contexts.append(get_current_context()),
    )
    await _add_event(session_factory, registry)
    outer_context = RequestContext(
        request_id="req-outer",
        tenant_id="tenant-outer",
    ).freeze()
    token = set_current_context(outer_context)
    try:
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            stats = await OutboxDispatcher(
                OutboxRepository(uow.session, registry=registry),
                registry,
                dispatcher_id="dispatcher-1",
            ).dispatch_once()

        assert stats.published == 1
        assert get_current_context() == outer_context
    finally:
        reset_current_context(token)

    assert len(seen_contexts) == 1
    context = seen_contexts[0]
    assert context is not None
    assert context.request_id == "req_test"
    assert context.trace_id == "trace_test"
    assert context.user_id == "user-1"
    assert context.tenant_id == "tenant-a"
    assert context.route == "outbox:business.created:v1"
    assert context.method == "OUTBOX"
    assert context.frozen is True


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
async def test_dispatcher_skips_handler_when_event_handler_already_succeeded(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    delivered: list[str] = []
    handler_key = "fake_idempotent_handlers.handle_business_created"
    registry = EventRegistry()
    _install_handler_module(
        monkeypatch,
        handle_business_created=lambda event: delivered.append(event.event_id),
    )
    registry.register_spec(
        "idempotent_app",
        EventHandlerSpec(
            event_type="business.created",
            event_version=1,
            handler_path=handler_key,
        ),
    )
    event_id = await _add_event(session_factory, registry)
    await _mark_handler_succeeded(
        session_factory,
        event_id=event_id,
        handler_key=handler_key,
    )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        stats = await OutboxDispatcher(
            OutboxRepository(uow.session, registry=registry),
            registry,
            dispatcher_id="dispatcher-1",
        ).dispatch_once()

    event = await _first_event(session_factory)
    assert stats.published == 1
    assert delivered == []
    assert event.status == "published"


@pytest.mark.asyncio
async def test_dispatcher_moves_exhausted_event_to_dead_letter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    metrics = MetricsRegistry()
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
            metrics=metrics,
        )
        stats = await dispatcher.dispatch_once()

    event = await _first_event(session_factory)
    rendered_metrics = metrics.render()
    assert stats.failed == 1
    assert stats.dead_lettered == 1
    assert event.status == "dead_letter"
    assert event.dead_letter_reason is not None
    assert 'outbox_dispatch_events_total{outcome="failed"} 1' in rendered_metrics
    assert 'outbox_dispatch_events_total{outcome="dead_lettered"} 1' in rendered_metrics
    assert "outbox_events_dead_letter 1" in rendered_metrics


@pytest.mark.asyncio
async def test_dispatcher_dead_letters_permanent_handler_error_without_retry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = EventRegistry()

    def permanent_failure(event: EventEnvelope) -> None:
        raise EventHandlerPermanentError("recipient is not valid")

    registry.register("business.created", 1, permanent_failure)
    await _add_event(session_factory, registry, max_attempts=3)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        stats = await OutboxDispatcher(
            OutboxRepository(uow.session, registry=registry),
            registry,
            dispatcher_id="dispatcher-1",
            retry_delay_seconds=0,
        ).dispatch_once()

    event = await _first_event(session_factory)
    assert stats.failed == 1
    assert stats.dead_lettered == 1
    assert event.status == "dead_letter"
    assert event.attempt_count == 1
    assert event.next_retry_at is None
    assert event.dead_letter_reason is not None
    assert "permanent" in event.dead_letter_reason
    assert "recipient is not valid" in event.dead_letter_reason


@pytest.mark.asyncio
async def test_dispatcher_dead_letters_schema_mismatch_before_calling_handler(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    called: list[str] = []
    registry = EventRegistry()
    registry.register_schema(
        EventSchemaSpec(
            event_type="business.created",
            event_version=1,
            required_payload_fields=["record_id"],
            field_types={"record_id": "str"},
        )
    )
    registry.register("business.created", 1, lambda event: called.append(event.event_id))
    await _add_raw_event(session_factory, max_attempts=3)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        stats = await OutboxDispatcher(
            OutboxRepository(uow.session, registry=registry),
            registry,
            dispatcher_id="dispatcher-1",
            retry_delay_seconds=0,
        ).dispatch_once()

    event = await _first_event(session_factory)
    assert stats.failed == 1
    assert stats.dead_lettered == 1
    assert called == []
    assert event.status == "dead_letter"
    assert event.attempt_count == 1
    assert event.dead_letter_reason is not None
    assert "EventPayloadValidationError" in event.dead_letter_reason
    assert "record_id" in event.dead_letter_reason


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


@pytest.mark.asyncio
async def test_claim_batch_does_not_reclaim_locked_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: None)
    await _add_event(session_factory, registry)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        first_claim = await OutboxRepository(uow.session, registry=registry).claim_batch(
            dispatcher_id="dispatcher-1",
            batch_size=1,
            lock_seconds=60,
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        second_claim = await OutboxRepository(uow.session, registry=registry).claim_batch(
            dispatcher_id="dispatcher-2",
            batch_size=1,
            lock_seconds=60,
        )

    event = await _first_event(session_factory)
    assert len(first_claim) == 1
    assert second_claim == []
    assert event.locked_by == "dispatcher-1"


@pytest.mark.asyncio
async def test_claim_batch_recovers_expired_publishing_lock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: None)
    await _add_event(session_factory, registry)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await OutboxRepository(uow.session, registry=registry).claim_batch(
            dispatcher_id="dispatcher-1",
            batch_size=1,
            lock_seconds=-1,
            now=datetime.now(UTC),
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        recovered_claim = await OutboxRepository(uow.session, registry=registry).claim_batch(
            dispatcher_id="dispatcher-2",
            batch_size=1,
            lock_seconds=60,
            now=datetime.now(UTC),
        )

    event = await _first_event(session_factory)
    assert len(recovered_claim) == 1
    assert event.locked_by == "dispatcher-2"


@pytest.mark.asyncio
async def test_mark_published_rejects_unclaimed_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: None)
    await _add_event(session_factory, registry)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        event = await uow.session.get(OutboxEvent, (await _first_event(session_factory)).id)
        assert event is not None
        with pytest.raises(AppError) as exc_info:
            await OutboxRepository(uow.session, registry=registry).mark_published(
                event,
                dispatcher_id="dispatcher-1",
            )

    current = await _first_event(session_factory)
    assert exc_info.value.code == "CONFLICT"
    assert current.status == "pending"
    assert current.published_at is None


@pytest.mark.asyncio
async def test_mark_published_rejects_non_owner_dispatcher(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: None)
    await _add_event(session_factory, registry)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        claimed = await OutboxRepository(uow.session, registry=registry).claim_batch(
            dispatcher_id="dispatcher-1",
            batch_size=1,
        )
        assert len(claimed) == 1
        with pytest.raises(AppError) as exc_info:
            await OutboxRepository(uow.session, registry=registry).mark_published(
                claimed[0],
                dispatcher_id="dispatcher-2",
            )

    current = await _first_event(session_factory)
    assert exc_info.value.code == "CONFLICT"
    assert current.status == "publishing"
    assert current.locked_by == "dispatcher-1"
    assert current.published_at is None


@pytest.mark.asyncio
async def test_expired_old_dispatcher_lease_cannot_mark_reclaimed_event_published(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: None)
    await _add_event(session_factory, registry)
    claimed_at = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)

    old_session = session_factory()
    try:
        old_repository = OutboxRepository(old_session, registry=registry)
        old_claim = await old_repository.claim_batch(
            dispatcher_id="dispatcher-1",
            batch_size=1,
            lock_seconds=1,
            now=claimed_at,
        )
        assert len(old_claim) == 1
        old_event = old_claim[0]
        await old_session.commit()

        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            new_claim = await OutboxRepository(uow.session, registry=registry).claim_batch(
                dispatcher_id="dispatcher-2",
                batch_size=1,
                lock_seconds=60,
                now=claimed_at + timedelta(seconds=2),
            )
            assert len(new_claim) == 1

        with pytest.raises(AppError) as exc_info:
            await old_repository.mark_published(
                old_event,
                dispatcher_id="dispatcher-1",
                now=claimed_at + timedelta(seconds=3),
            )

        current = await _first_event(session_factory)
        assert exc_info.value.code == "CONFLICT"
        assert current.status == "publishing"
        assert current.locked_by == "dispatcher-2"
        assert current.published_at is None
    finally:
        await old_session.close()


@pytest.mark.asyncio
async def test_mark_failed_rejects_completed_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: None)
    await _add_event(session_factory, registry)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        repository = OutboxRepository(uow.session, registry=registry)
        claimed = await repository.claim_batch(dispatcher_id="dispatcher-1", batch_size=1)
        assert len(claimed) == 1
        await repository.mark_published(claimed[0], dispatcher_id="dispatcher-1")

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        event = await uow.session.get(OutboxEvent, (await _first_event(session_factory)).id)
        assert event is not None
        with pytest.raises(AppError) as exc_info:
            await OutboxRepository(uow.session, registry=registry).mark_failed(
                event,
                RuntimeError("late failure"),
                dispatcher_id="dispatcher-1",
            )

    current = await _first_event(session_factory)
    assert exc_info.value.code == "CONFLICT"
    assert current.status == "published"
    assert current.attempt_count == 0


async def _add_event(
    session_factory: async_sessionmaker[AsyncSession],
    registry: EventRegistry,
    *,
    max_attempts: int = 3,
) -> str:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        event = await OutboxRepository(uow.session, registry=registry).add(
            event_type="business.created",
            aggregate_type="business_record",
            aggregate_id="record-1",
            tenant_id="tenant-a",
            payload=_payload(),
            max_attempts=max_attempts,
        )
        await uow.session.flush()
        return event.id


async def _add_raw_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    max_attempts: int = 3,
) -> str:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        event = OutboxEvent(
            event_type="business.created",
            event_version=1,
            aggregate_type="business_record",
            aggregate_id="record-1",
            tenant_id="tenant-a",
            payload=_payload(),
            max_attempts=max_attempts,
            status="pending",
        )
        uow.session.add(event)
        await uow.session.flush()
        return event.id


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
        "trace_id": "trace_test",
        **overrides,
    }


def _install_handler_module(monkeypatch: pytest.MonkeyPatch, **handlers: object) -> None:
    import sys
    import types

    handler_module = types.ModuleType("fake_idempotent_handlers")
    for name, handler in handlers.items():
        setattr(handler_module, name, handler)
    monkeypatch.setitem(sys.modules, "fake_idempotent_handlers", handler_module)


async def _mark_handler_succeeded(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    event_id: str,
    handler_key: str,
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        store = IdempotencyStore(uow.session)
        claim = await store.claim(
            tenant_id="tenant-a",
            user_id="user-1",
            route=f"outbox:business.created:v1:{handler_key}",
            idempotency_key=event_id,
            request_hash=hash_request_payload(
                {
                    "event_id": event_id,
                    "event_type": "business.created",
                    "event_version": 1,
                    "handler_key": handler_key,
                }
            ),
        )
        await store.mark_succeeded(
            claim.record,
            response_code="OK",
            response_body={"event_id": event_id, "handler_key": handler_key},
            outbox_event_id=event_id,
        )
