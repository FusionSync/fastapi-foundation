from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.exceptions import AppError

UnitOfWorkState = Literal["new", "active", "committed", "rolled_back", "joined"]
_current_unit_of_work: ContextVar[UnitOfWork | None] = ContextVar(
    "current_unit_of_work",
    default=None,
)


class TenantFallbackApplier(Protocol):
    async def apply(self, session: AsyncSession, tenant_id: str) -> None: ...


class UnitOfWork:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        *,
        tenant_fallback: TenantFallbackApplier | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.tenant_fallback = tenant_fallback
        self.tenant_id = tenant_id
        self.session: AsyncSession | None = None
        self.state: UnitOfWorkState = "new"
        self.rollback_only = False
        self._parent: UnitOfWork | None = None
        self._token: Token[UnitOfWork | None] | None = None

    async def __aenter__(self) -> UnitOfWork:
        active = _current_unit_of_work.get()
        if active is not None and active.session is not None and active.state == "active":
            if self.tenant_id and active.tenant_id and self.tenant_id != active.tenant_id:
                raise AppError(
                    "TENANT_CONTEXT_CONFLICT",
                    "Nested unit of work tenant_id conflicts with active transaction",
                    status_code=403,
                )
            self.session = active.session
            self._parent = active
            self.tenant_id = self.tenant_id or active.tenant_id
            self.tenant_fallback = active.tenant_fallback
            self.state = "active"
            self._token = _current_unit_of_work.set(self)
            return self

        self.session = self.session_factory()
        if self.tenant_id and self.tenant_fallback is not None:
            await self.tenant_fallback.apply(self.session, self.tenant_id)
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


def unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tenant_fallback: TenantFallbackApplier | None = None,
    tenant_id: str | None = None,
) -> UnitOfWork:
    return UnitOfWork(
        session_factory,
        tenant_fallback=tenant_fallback,
        tenant_id=tenant_id,
    )
