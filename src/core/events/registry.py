from __future__ import annotations

import importlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.exceptions import AppError

if TYPE_CHECKING:
    from core.apps.module import EventHandlerSpec
    from core.apps.registry import AppRegistry

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


@dataclass(frozen=True, slots=True)
class RegisteredEventHandler:
    app_label: str
    spec: EventHandlerSpec
    handler: EventHandler


class EventRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], list[EventHandler]] = {}
        self._registered_handlers: list[RegisteredEventHandler] = []
        self._handler_keys: set[tuple[str, int, str]] = set()

    @classmethod
    def from_app_registry(cls, app_registry: AppRegistry) -> EventRegistry:
        registry = cls()
        for app_module in app_registry.modules:
            for spec in app_module.event_handlers:
                registry.register_spec(app_module.label, spec)
        return registry

    def register(self, event_type: str, event_version: int, handler: EventHandler) -> None:
        key = (event_type, event_version)
        self._handlers.setdefault(key, []).append(handler)

    def register_spec(self, app_label: str, spec: EventHandlerSpec) -> None:
        duplicate_key = (spec.event_type, spec.event_version, spec.handler_path)
        if duplicate_key in self._handler_keys:
            raise ValueError(
                "Duplicate event handler "
                f"{spec.handler_path!r} for {spec.event_type} v{spec.event_version}"
            )
        handler = _import_event_handler(spec.handler_path)
        self.register(spec.event_type, spec.event_version, handler)
        self._handler_keys.add(duplicate_key)
        self._registered_handlers.append(
            RegisteredEventHandler(
                app_label=app_label,
                spec=spec,
                handler=handler,
            )
        )

    def has_event_type(self, event_type: str, event_version: int) -> bool:
        return (event_type, event_version) in self._handlers

    def to_dict(self) -> dict[str, object]:
        return {
            "handlers": [
                {
                    "app_label": registered.app_label,
                    "event_type": registered.spec.event_type,
                    "event_version": registered.spec.event_version,
                    "handler_path": registered.spec.handler_path,
                }
                for registered in self._registered_handlers
            ]
        }

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


def _import_event_handler(handler_path: str) -> EventHandler:
    module_path, _, attribute = handler_path.rpartition(".")
    if not module_path or not attribute:
        raise ValueError(f"Invalid event handler path: {handler_path!r}")
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        raise ImportError(f"Failed to import event handler {handler_path!r}: {exc}") from exc
    handler = getattr(module, attribute, None)
    if not callable(handler):
        raise TypeError(f"Event handler {handler_path!r} must be callable")
    return handler
