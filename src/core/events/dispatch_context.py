from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

_current_event_dispatch_session: ContextVar[AsyncSession | None] = ContextVar(
    "current_event_dispatch_session",
    default=None,
)


@contextmanager
def use_event_dispatch_session(session: AsyncSession) -> Any:
    token: Token[AsyncSession | None] = _current_event_dispatch_session.set(session)
    try:
        yield
    finally:
        _current_event_dispatch_session.reset(token)


def get_current_event_dispatch_session() -> AsyncSession | None:
    return _current_event_dispatch_session.get()
