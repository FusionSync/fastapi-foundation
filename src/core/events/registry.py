from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from core.exceptions import AppError

EventHandler = Callable[["EventEnvelope"], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    event_type: str
    event_version: int
    tenant_id: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]


class EventRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], list[EventHandler]] = {}

    def register(self, event_type: str, event_version: int, handler: EventHandler) -> None:
        key = (event_type, event_version)
        self._handlers.setdefault(key, []).append(handler)

    def has_event_type(self, event_type: str, event_version: int) -> bool:
        return (event_type, event_version) in self._handlers

    async def dispatch(self, envelope: EventEnvelope) -> None:
        handlers = self._handlers.get((envelope.event_type, envelope.event_version), [])
        if not handlers:
            raise AppError(
                "SYSTEM_ERROR",
                f"No handler registered for {envelope.event_type} v{envelope.event_version}",
                status_code=500,
            )
        for handler in handlers:
            result = handler(envelope)
            if inspect.isawaitable(result):
                await result
