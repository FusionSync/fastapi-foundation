from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import BaseModel
from core.db import unit_of_work


class TransactionRecord(BaseModel):
    __tablename__ = "test_transaction_records"

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
async def test_nested_unit_of_work_reuses_outer_session_and_rolls_back_together(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(RuntimeError, match="rollback outer"):
        async with unit_of_work(session_factory) as outer:
            assert outer.session is not None
            outer.session.add(TransactionRecord(name="outer"))
            async with unit_of_work(session_factory) as inner:
                assert inner.session is outer.session
                inner.session.add(TransactionRecord(name="inner"))
            raise RuntimeError("rollback outer")

    assert await _count(session_factory) == 0


@pytest.mark.asyncio
async def test_nested_unit_of_work_exception_marks_outer_rollback_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as outer:
        assert outer.session is not None
        outer.session.add(TransactionRecord(name="outer"))
        with pytest.raises(RuntimeError, match="inner failure"):
            async with unit_of_work(session_factory) as inner:
                assert inner.session is outer.session
                inner.session.add(TransactionRecord(name="inner"))
                raise RuntimeError("inner failure")
        assert outer.rollback_only is True

    assert await _count(session_factory) == 0


async def _count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        result = await session.scalar(select(func.count()).select_from(TransactionRecord))
        return int(result or 0)
