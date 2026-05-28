from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import Settings


@dataclass(frozen=True, slots=True)
class DatabaseRuntime:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    async def dispose(self) -> None:
        await self.engine.dispose()


def create_database_runtime(settings: Settings) -> DatabaseRuntime:
    engine = create_async_engine(settings.database.url)
    return DatabaseRuntime(
        engine=engine,
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
    )
