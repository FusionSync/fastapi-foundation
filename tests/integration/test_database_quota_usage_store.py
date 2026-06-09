from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import Model
from core.exceptions import AppError
from core.quotas import DatabaseQuotaUsageStore


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
async def test_database_quota_usage_store_reserves_without_exceeding_limit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = DatabaseQuotaUsageStore(session_factory)
    key = "quota:file_count:tenant_id=tenant-a"

    first = await store.reserve(key, amount=2, limit=3)
    denied = await store.reserve(key, amount=2, limit=3)

    assert first == 2
    assert denied is None
    assert await store.get_usage(key) == 2


@pytest.mark.asyncio
async def test_database_quota_usage_store_set_release_and_validation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    store = DatabaseQuotaUsageStore(session_factory)
    key = "quota:concurrent_tasks:tenant_id=tenant-a:user_id=user-1"

    await store.set_usage(key, 5)

    assert await store.release(key, amount=2) == 3
    assert await store.release(key, amount=10) == 0
    assert await store.get_usage(key) == 0
    with pytest.raises(AppError) as invalid:
        await store.set_usage(key, -1)

    assert invalid.value.code == "VALIDATION_ERROR"
