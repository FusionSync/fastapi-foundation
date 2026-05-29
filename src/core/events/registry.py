from __future__ import annotations

import importlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.context import outbox_background_context, use_background_context
from core.events.errors import EventPayloadValidationError, EventSchemaCompatibilityError
from core.exceptions import AppError
from core.idempotency import IdempotencyStore, hash_request_payload

if TYPE_CHECKING:
    from core.apps.module import EventHandlerSpec, EventSchemaSpec
    from core.apps.registry import AppRegistry

EventHandler = Callable[["EventEnvelope"], Awaitable[None] | None]
_CORE_REQUIRED_PAYLOAD_FIELDS = ("tenant_id", "actor_id", "request_id")
_SUPPORTED_FIELD_TYPES = {"str", "int", "float", "number", "bool", "dict", "list"}


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


@dataclass(frozen=True, slots=True)
class _EventSchemaEntry:
    schema: Any
    explicit: bool


@dataclass(frozen=True, slots=True)
class _RuntimeEventSchema:
    event_type: str
    event_version: int
    required_payload_fields: list[str]
    field_types: dict[str, str]
    compatible_with: list[int]

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "event_version": self.event_version,
            "required_payload_fields": self.required_payload_fields,
            "field_types": self.field_types,
            "compatible_with": self.compatible_with,
        }


class EventRegistry:
    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], list[_EventHandlerEntry]] = {}
        self._schemas: dict[tuple[str, int], _EventSchemaEntry] = {}
        self._registered_schemas: list[Any] = []
        self._registered_handlers: list[RegisteredEventHandler] = []
        self._handler_keys: set[tuple[str, int, str]] = set()

    @classmethod
    def from_app_registry(cls, app_registry: AppRegistry) -> EventRegistry:
        registry = cls()
        for app_module in app_registry.modules:
            for schema in app_module.event_schemas:
                registry.register_schema(schema)
            for spec in app_module.event_handlers:
                registry.register_spec(app_module.label, spec)
        return registry

    def register_schema(self, schema: EventSchemaSpec) -> None:
        self._register_schema(schema, explicit=True)

    def register(
        self,
        event_type: str,
        event_version: int,
        handler: EventHandler,
        *,
        handler_key: str | None = None,
    ) -> None:
        key = (event_type, event_version)
        self._ensure_minimal_schema(event_type, event_version)
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
        return (event_type, event_version) in self._schemas

    def validate_event(
        self,
        *,
        event_type: str,
        event_version: int,
        tenant_id: str,
        payload: dict[str, Any],
    ) -> None:
        schema_entry = self._schemas.get((event_type, event_version))
        if schema_entry is None:
            raise EventPayloadValidationError(
                f"Unregistered event type: {event_type} v{event_version}"
            )
        schema = schema_entry.schema
        missing_fields = _required_payload_fields(schema) - set(payload)
        if missing_fields:
            raise EventPayloadValidationError(
                "Event payload missing required fields for "
                f"{event_type} v{event_version}: {sorted(missing_fields)}"
            )
        if payload["tenant_id"] != tenant_id:
            raise EventPayloadValidationError(
                "Event payload tenant_id must match outbox tenant_id"
            )
        for field_name, expected_type in schema.field_types.items():
            if field_name not in payload:
                continue
            if not _matches_field_type(payload[field_name], expected_type):
                raise EventPayloadValidationError(
                    "Event payload field "
                    f"{field_name!r} for {event_type} v{event_version} "
                    f"must be {expected_type}"
                )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
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
        if self._registered_schemas:
            payload["schemas"] = [schema.to_dict() for schema in self._registered_schemas]
        return payload

    async def dispatch(
        self,
        envelope: EventEnvelope,
        *,
        idempotency_store: IdempotencyStore | None = None,
    ) -> None:
        self.validate_event(
            event_type=envelope.event_type,
            event_version=envelope.event_version,
            tenant_id=envelope.tenant_id,
            payload=envelope.payload,
        )
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

    def _ensure_minimal_schema(self, event_type: str, event_version: int) -> None:
        if (event_type, event_version) not in self._schemas:
            self._register_schema(
                _RuntimeEventSchema(
                    event_type=event_type,
                    event_version=event_version,
                    required_payload_fields=[],
                    field_types={},
                    compatible_with=[],
                ),
                explicit=False,
            )

    def _register_schema(self, schema: Any, *, explicit: bool) -> None:
        if schema.event_version < 1:
            raise ValueError("Event schema version must be positive")
        for field_name in schema.required_payload_fields:
            if not isinstance(field_name, str) or not field_name:
                raise ValueError("Event schema required payload fields must be non-empty strings")
        for field_name, field_type in schema.field_types.items():
            if not isinstance(field_name, str) or not field_name:
                raise ValueError("Event schema field names must be non-empty strings")
            if field_type not in _SUPPORTED_FIELD_TYPES:
                raise ValueError(f"Unsupported event schema field type: {field_type}")
        key = (schema.event_type, schema.event_version)
        existing = self._schemas.get(key)
        if existing is not None and (existing.explicit or not explicit):
            if explicit and existing.explicit:
                raise ValueError(
                    f"Duplicate event schema for {schema.event_type} v{schema.event_version}"
                )
            return
        self._validate_schema_compatibility(schema)
        self._schemas[key] = _EventSchemaEntry(schema=schema, explicit=explicit)
        if explicit:
            self._registered_schemas.append(schema)

    def _validate_schema_compatibility(self, schema: Any) -> None:
        for compatible_version in schema.compatible_with:
            compatible_entry = self._schemas.get((schema.event_type, compatible_version))
            if compatible_entry is None:
                raise EventSchemaCompatibilityError(
                    f"Event schema {schema.event_type} v{schema.event_version} "
                    f"declares missing compatible version {compatible_version}"
                )
            compatible_schema = compatible_entry.schema
            missing_required_fields = _required_payload_fields(compatible_schema) - (
                _required_payload_fields(schema)
            )
            if missing_required_fields:
                raise EventSchemaCompatibilityError(
                    f"Event schema {schema.event_type} v{schema.event_version} "
                    f"removes required fields from compatible version "
                    f"{compatible_version}: {sorted(missing_required_fields)}"
                )
            for field_name, expected_type in compatible_schema.field_types.items():
                actual_type = schema.field_types.get(field_name)
                if actual_type is not None and actual_type != expected_type:
                    raise EventSchemaCompatibilityError(
                        f"Event schema {schema.event_type} v{schema.event_version} "
                        f"changes field type for {field_name!r} from "
                        f"{expected_type} to {actual_type}"
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


def _required_payload_fields(schema: Any) -> set[str]:
    return set(_CORE_REQUIRED_PAYLOAD_FIELDS) | set(schema.required_payload_fields)


def _matches_field_type(value: object, expected_type: str) -> bool:
    if expected_type == "str":
        return isinstance(value, str)
    if expected_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "float":
        return isinstance(value, float)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "bool":
        return isinstance(value, bool)
    if expected_type == "dict":
        return isinstance(value, dict)
    if expected_type == "list":
        return isinstance(value, list)
    return False


async def _call_handler(handler: EventHandler, envelope: EventEnvelope) -> None:
    with use_background_context(
        outbox_background_context(
            event_id=envelope.event_id,
            event_type=envelope.event_type,
            event_version=envelope.event_version,
            tenant_id=envelope.tenant_id,
            payload=envelope.payload,
        )
    ):
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
