from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import Model
from core.db import unit_of_work
from core.events import EventRegistry
from core.mq import MqPublishRequest
from core.outbox import OutboxDispatcher, OutboxEvent, OutboxRepository, RabbitMqOutboxPublisher


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
async def test_outbox_dispatcher_can_relay_claimed_events_to_rabbitmq(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    mq_client = FakeMqClient()
    registry = EventRegistry()
    registry.register("business.created", 1, lambda _envelope: None)

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        event = await OutboxRepository(uow.session, registry=registry).add(
            event_type="business.created",
            event_version=1,
            aggregate_type="business",
            aggregate_id="business-1",
            tenant_id="tenant-a",
            payload={
                "tenant_id": "tenant-a",
                "actor_id": "user-1",
                "request_id": "req-outbox",
                "name": "Created",
            },
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        stats = await OutboxDispatcher(
            OutboxRepository(uow.session, registry=registry),
            registry,
            dispatcher_id="rabbitmq-relay",
            external_publisher=RabbitMqOutboxPublisher(
                mq_client,
                exchange="foundation.events",
                routing_key_template="{event_type}",
            ),
        ).dispatch_once()

    async with session_factory() as session:
        persisted = await session.scalar(select(OutboxEvent).where(OutboxEvent.id == event.id))
        assert persisted is not None
        assert persisted.status == "published"

    assert stats.claimed == 1
    assert stats.published == 1
    assert stats.failed == 0
    assert len(mq_client.published) == 1
    published = mq_client.published[0]
    assert published.exchange == "foundation.events"
    assert published.routing_key == "business.created"
    assert published.headers == {
        "event_id": event.id,
        "event_type": "business.created",
        "event_version": 1,
        "tenant_id": "tenant-a",
    }
    assert published.json()["payload"]["name"] == "Created"
    assert published.json()["event_id"] == event.id


class FakeMqClient:
    def __init__(self) -> None:
        self.published: list[MqPublishRequest] = []

    async def publish(self, request: MqPublishRequest):
        self.published.append(request)
        return None
