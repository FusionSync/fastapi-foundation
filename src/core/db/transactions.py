from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

UnitOfWorkState = Literal["new", "active", "committed", "rolled_back", "joined"]
_current_unit_of_work: ContextVar[UnitOfWork | None] = ContextVar(
    "current_unit_of_work",
    default=None,
)


class UnitOfWork:
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self.session_factory = session_factory
        self.session: AsyncSession | None = None
        self.state: UnitOfWorkState = "new"
        self.rollback_only = False
        self._parent: UnitOfWork | None = None
        self._token: Token[UnitOfWork | None] | None = None

    async def __aenter__(self) -> UnitOfWork:
        active = _current_unit_of_work.get()
        if active is not None and active.session is not None and active.state == "active":
            self.session = active.session
            self._parent = active
            self.state = "active"
            self._token = _current_unit_of_work.set(self)
            return self

        self.session = self.session_factory()
        self.state = "active"
        self._token = _current_unit_of_work.set(self)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        if self._parent is not None:
            if exc_type is not None:
                self.mark_rollback_only()
                self.state = "rolled_back"
            else:
                self.state = "joined"
            self._reset_context()
            return

        try:
            if exc_type is None and not self.rollback_only:
                await self.session.commit()
                self.state = "committed"
            else:
                await self.session.rollback()
                self.state = "rolled_back"
        finally:
            await self.session.close()
            self._reset_context()

    def mark_rollback_only(self) -> None:
        self.rollback_only = True
        if self._parent is not None:
            self._parent.mark_rollback_only()

    def _reset_context(self) -> None:
        if self._token is None:
            return
        _current_unit_of_work.reset(self._token)
        self._token = None


def unit_of_work(session_factory: async_sessionmaker[AsyncSession]) -> UnitOfWork:
    return UnitOfWork(session_factory)
