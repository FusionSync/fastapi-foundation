from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import BaseModel, TenantScopedModel
from core.db import unit_of_work
from core.events import EventRegistry
from core.exceptions import AppError
from core.outbox import OutboxEvent, OutboxEventPublisher, OutboxRepository


class BusinessRecord(TenantScopedModel):
    __tablename__ = "test_outbox_business_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(64), nullable=False)


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
async def test_committed_business_write_leaves_outbox_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = _registry()

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(BusinessRecord(tenant_id="tenant-a", name="demo"))
        await OutboxRepository(uow.session, registry=registry).add(
            event_type="business.created",
            aggregate_type="business_record",
            aggregate_id="record-1",
            tenant_id="tenant-a",
            payload=_payload(),
        )

    assert await _count(session_factory, BusinessRecord) == 1
    assert await _count(session_factory, OutboxEvent) == 1


@pytest.mark.asyncio
async def test_event_publish_api_writes_outbox_event_in_same_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = _registry()

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(BusinessRecord(tenant_id="tenant-a", name="demo"))
        await OutboxEventPublisher(OutboxRepository(uow.session, registry=registry)).publish(
            event_type="business.created",
            aggregate_type="business_record",
            aggregate_id="record-1",
            tenant_id="tenant-a",
            payload=_payload(),
        )

    events = await _all(session_factory, OutboxEvent)
    assert await _count(session_factory, BusinessRecord) == 1
    assert [(event.event_type, event.aggregate_id, event.status) for event in events] == [
        ("business.created", "record-1", "pending")
    ]


@pytest.mark.asyncio
async def test_rolled_back_business_write_does_not_leave_outbox_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = _registry()

    with pytest.raises(RuntimeError):
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(BusinessRecord(tenant_id="tenant-a", name="demo"))
            await OutboxRepository(uow.session, registry=registry).add(
                event_type="business.created",
                aggregate_type="business_record",
                aggregate_id="record-1",
                tenant_id="tenant-a",
                payload=_payload(),
            )
            raise RuntimeError("rollback")

    assert await _count(session_factory, BusinessRecord) == 0
    assert await _count(session_factory, OutboxEvent) == 0


@pytest.mark.asyncio
async def test_outbox_rejects_unregistered_event_type(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError, match="Unregistered event type"):
            await OutboxRepository(uow.session, registry=EventRegistry()).add(
                event_type="business.created",
                aggregate_type="business_record",
                aggregate_id="record-1",
                tenant_id="tenant-a",
                payload=_payload(),
            )


async def _count(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[BaseModel],
) -> int:
    async with session_factory() as session:
        result = await session.scalar(select(func.count()).select_from(model))
        return int(result or 0)


async def _all(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[BaseModel],
) -> list[BaseModel]:
    async with session_factory() as session:
        result = await session.execute(select(model))
        rows = list(result.scalars().all())
        for row in rows:
            session.expunge(row)
        return rows


def _registry() -> EventRegistry:
    registry = EventRegistry()
    registry.register("business.created", 1, lambda event: None)
    return registry


def _payload(**overrides: Any) -> dict[str, Any]:
    return {
        "tenant_id": "tenant-a",
        "actor_id": "user-1",
        "request_id": "req_test",
        **overrides,
    }
