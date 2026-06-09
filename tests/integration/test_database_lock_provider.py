from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import Model
from core.exceptions import AppError
from core.locks import DatabaseLockProvider


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 5, 28, tzinfo=UTC)

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


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
async def test_database_lock_acquire_release_and_owner_validation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    locks = DatabaseLockProvider(session_factory, clock=lambda: clock.now)

    acquired = await locks.acquire(
        "schedule:daily-close",
        owner_token="owner-1",
        ttl_seconds=30,
    )
    blocked = await locks.acquire(
        "schedule:daily-close",
        owner_token="owner-2",
        ttl_seconds=30,
    )
    assert blocked.acquired is False
    assert blocked.fencing_token == 1
    assert await locks.locked("schedule:daily-close") is True
    assert await locks.release("schedule:daily-close", owner_token="owner-2") is False
    assert await locks.release("schedule:daily-close", owner_token="owner-1") is True
    assert await locks.locked("schedule:daily-close") is False
    reacquired = await locks.acquire(
        "schedule:daily-close",
        owner_token="owner-2",
        ttl_seconds=30,
    )

    assert acquired.acquired is True
    assert acquired.lock_key == "schedule:daily-close"
    assert acquired.owner_token == "owner-1"
    assert acquired.expires_at == clock.now + timedelta(seconds=30)
    assert acquired.fencing_token == 1
    assert reacquired.acquired is True
    assert reacquired.owner_token == "owner-2"
    assert reacquired.fencing_token == 2


@pytest.mark.asyncio
async def test_database_lock_extend_requires_current_owner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    locks = DatabaseLockProvider(session_factory, clock=lambda: clock.now)

    acquired = await locks.acquire(
        "file:file-1:process",
        owner_token="owner-1",
        ttl_seconds=30,
    )
    wrong_owner = await locks.extend(
        "file:file-1:process",
        owner_token="owner-2",
        ttl_seconds=60,
    )
    extended = await locks.extend(
        "file:file-1:process",
        owner_token="owner-1",
        ttl_seconds=60,
    )

    assert acquired.fencing_token == 1
    assert wrong_owner is None
    assert extended is not None
    assert extended.acquired is True
    assert extended.expires_at == clock.now + timedelta(seconds=60)
    assert extended.fencing_token == 1


@pytest.mark.asyncio
async def test_database_lock_expired_lock_can_be_reacquired_with_new_fencing_token(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    clock = Clock()
    locks = DatabaseLockProvider(session_factory, clock=lambda: clock.now)

    first = await locks.acquire("worker:tenant-a", owner_token="worker-1", ttl_seconds=10)
    clock.advance(11)
    second = await locks.acquire("worker:tenant-a", owner_token="worker-2", ttl_seconds=30)

    assert first.fencing_token == 1
    assert second.acquired is True
    assert second.owner_token == "worker-2"
    assert second.fencing_token == 2


@pytest.mark.asyncio
async def test_database_lock_require_acquire_raises_stable_code_when_locked(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    locks = DatabaseLockProvider(session_factory)
    await locks.acquire("scheduler:hourly", owner_token="owner-1", ttl_seconds=60)

    with pytest.raises(AppError) as locked:
        await locks.require_acquire(
            "scheduler:hourly",
            owner_token="owner-2",
            ttl_seconds=60,
        )

    assert locked.value.code == "LOCK_NOT_ACQUIRED"
    assert locked.value.details == {"lock_key": "scheduler:hourly"}


@pytest.mark.asyncio
async def test_database_lock_rejects_invalid_ttl_and_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    locks = DatabaseLockProvider(session_factory)

    with pytest.raises(AppError) as invalid_ttl:
        await locks.acquire("scheduler:hourly", owner_token="owner-1", ttl_seconds=0)
    with pytest.raises(AppError) as invalid_key:
        await locks.acquire(" ", owner_token="owner-1", ttl_seconds=60)

    assert invalid_ttl.value.code == "VALIDATION_ERROR"
    assert invalid_key.value.code == "VALIDATION_ERROR"
