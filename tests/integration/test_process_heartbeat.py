from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.operations import ProcessHeartbeatRepository, check_process_health


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
async def test_process_heartbeat_marks_worker_health_fresh(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 5, 28, tzinfo=UTC)
    async with session_factory() as session:
        repository = ProcessHeartbeatRepository(session)
        await repository.record(
            role="worker",
            instance_id="worker-1",
            status="healthy",
            details={"queue": "default"},
            now=now,
        )
        await session.commit()
        heartbeat = await repository.latest("worker")

    health = check_process_health(
        "worker",
        heartbeat=heartbeat,
        now=now + timedelta(seconds=30),
        heartbeat_max_age_seconds=60,
    )

    assert health.ok is True
    assert health.checks["heartbeat_fresh"] is True
    assert health.details["heartbeat_instance_id"] == "worker-1"
    assert health.details["heartbeat_details"] == {"queue": "default"}


@pytest.mark.asyncio
async def test_process_heartbeat_marks_stale_role_unhealthy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime(2026, 5, 28, tzinfo=UTC)
    async with session_factory() as session:
        repository = ProcessHeartbeatRepository(session)
        await repository.record(
            role="outbox-dispatcher",
            instance_id="outbox-1",
            status="healthy",
            now=now,
        )
        await session.commit()
        heartbeat = await repository.latest("outbox-dispatcher")

    health = check_process_health(
        "outbox-dispatcher",
        heartbeat=heartbeat,
        now=now + timedelta(seconds=120),
        heartbeat_max_age_seconds=60,
    )

    assert health.ok is False
    assert health.checks["heartbeat_fresh"] is False
