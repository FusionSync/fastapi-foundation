from __future__ import annotations

import importlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.exceptions import AppError
from core.idempotency import IdempotencyStore, hash_request_payload

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


@dataclass(frozen=True, slots=True)
class _EventHandlerEntry:
    handler_key: str
    handler: EventHandler


class EventRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], list[_EventHandlerEntry]] = {}
        self._registered_handlers: list[RegisteredEventHandler] = []
        self._handler_keys: set[tuple[str, int, str]] = set()

    @classmethod
    def from_app_registry(cls, app_registry: AppRegistry) -> EventRegistry:
        registry = cls()
        for app_module in app_registry.modules:
            for spec in app_module.event_handlers:
                registry.register_spec(app_module.label, spec)
        return registry

    def register(
        self,
        event_type: str,
        event_version: int,
        handler: EventHandler,
        *,
        handler_key: str | None = None,
    ) -> None:
        key = (event_type, event_version)
        resolved_handler_key = handler_key or _handler_key(handler)
        if any(entry.handler_key == resolved_handler_key for entry in self._handlers.get(key, [])):
            raise ValueError(
                "Duplicate event handler "
                f"{resolved_handler_key!r} for {event_type} v{event_version}"
            )
        entry = _EventHandlerEntry(
            handler_key=resolved_handler_key,
            handler=handler,
        )
        self._handlers.setdefault(key, []).append(entry)

    def register_spec(self, app_label: str, spec: EventHandlerSpec) -> None:
        duplicate_key = (spec.event_type, spec.event_version, spec.handler_path)
        if duplicate_key in self._handler_keys:
            raise ValueError(
                "Duplicate event handler "
                f"{spec.handler_path!r} for {spec.event_type} v{spec.event_version}"
            )
        handler = _import_event_handler(spec.handler_path)
        self.register(
            spec.event_type,
            spec.event_version,
            handler,
            handler_key=spec.handler_path,
        )
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

    async def dispatch(
        self,
        envelope: EventEnvelope,
        *,
        idempotency_store: IdempotencyStore | None = None,
    ) -> None:
        entries = self._handlers.get((envelope.event_type, envelope.event_version), [])
        if not entries:
            raise AppError(
                "SYSTEM_ERROR",
                f"No handler registered for {envelope.event_type} v{envelope.event_version}",
                status_code=500,
            )
        for entry in entries:
            if idempotency_store is None:
                await _call_handler(entry.handler, envelope)
                continue
            claim = await idempotency_store.claim(
                tenant_id=envelope.tenant_id,
                user_id=str(envelope.payload.get("actor_id") or "__system__"),
                route=_handler_route(envelope, entry.handler_key),
                idempotency_key=envelope.event_id,
                request_hash=_handler_request_hash(envelope, entry.handler_key),
                retry_failed=True,
            )
            if claim.outcome == "replayed":
                continue
            try:
                await _call_handler(entry.handler, envelope)
            except Exception as exc:
                await idempotency_store.mark_failed(
                    claim.record,
                    response_code=type(exc).__name__,
                    response_body={"error": str(exc)},
                )
                raise
            await idempotency_store.mark_succeeded(
                claim.record,
                response_code="OK",
                response_body={
                    "event_id": envelope.event_id,
                    "handler_key": entry.handler_key,
                },
                outbox_event_id=envelope.event_id,
            )


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


async def _call_handler(handler: EventHandler, envelope: EventEnvelope) -> None:
    result = handler(envelope)
    if inspect.isawaitable(result):
        await result


def _handler_key(handler: EventHandler) -> str:
    module = getattr(handler, "__module__", handler.__class__.__module__)
    qualname = getattr(handler, "__qualname__", handler.__class__.__qualname__)
    return f"{module}.{qualname}"


def _handler_route(envelope: EventEnvelope, handler_key: str) -> str:
    return f"outbox:{envelope.event_type}:v{envelope.event_version}:{handler_key}"


def _handler_request_hash(envelope: EventEnvelope, handler_key: str) -> str:
    return hash_request_payload(
        {
            "event_id": envelope.event_id,
            "event_type": envelope.event_type,
            "event_version": envelope.event_version,
            "handler_key": handler_key,
        }
    )
