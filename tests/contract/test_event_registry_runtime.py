import sys
import types

import pytest

from core.apps import AppModule, AppRegistry, EventHandlerSpec, EventSchemaSpec
from core.events import (
    EventEnvelope,
    EventPayloadValidationError,
    EventRegistry,
    EventSchemaCompatibilityError,
)


@pytest.mark.asyncio
async def test_event_registry_connects_app_module_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered: list[str] = []

    async def handle_created(envelope: EventEnvelope) -> None:
        delivered.append(envelope.event_id)

    _install_handler_module(monkeypatch, handle_created=handle_created)
    _install_app(
        monkeypatch,
        event_handlers=[
            EventHandlerSpec(
                event_type="example.created",
                event_version=1,
                handler_path="fake_event_handlers.handle_created",
            )
        ],
    )

    app_registry = AppRegistry(["fake_event_app"]).load()
    event_registry = EventRegistry.from_app_registry(app_registry)
    await event_registry.dispatch(_envelope(event_id="event-1"))

    assert event_registry.has_event_type("example.created", 1) is True
    assert delivered == ["event-1"]
    assert event_registry.to_dict() == {
        "handlers": [
            {
                "app_label": "event_app",
                "event_type": "example.created",
                "event_version": 1,
                "handler_path": "fake_event_handlers.handle_created",
            }
        ]
    }


@pytest.mark.asyncio
async def test_event_registry_allows_multiple_handlers_for_same_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered: list[str] = []

    def first(envelope: EventEnvelope) -> None:
        delivered.append(f"first:{envelope.event_id}")

    def second(envelope: EventEnvelope) -> None:
        delivered.append(f"second:{envelope.event_id}")

    _install_handler_module(monkeypatch, first=first, second=second)
    _install_app(
        monkeypatch,
        event_handlers=[
            EventHandlerSpec(
                event_type="example.created",
                event_version=1,
                handler_path="fake_event_handlers.first",
            ),
            EventHandlerSpec(
                event_type="example.created",
                event_version=1,
                handler_path="fake_event_handlers.second",
            ),
        ],
    )

    event_registry = EventRegistry.from_app_registry(AppRegistry(["fake_event_app"]).load())
    await event_registry.dispatch(_envelope(event_id="event-1"))

    assert delivered == ["first:event-1", "second:event-1"]


def test_event_registry_collects_app_module_event_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_app(
        monkeypatch,
        event_schemas=[
            EventSchemaSpec(
                event_type="example.created",
                event_version=1,
                required_payload_fields=["resource_id"],
                field_types={"resource_id": "str"},
            )
        ],
        event_handlers=[],
    )

    event_registry = EventRegistry.from_app_registry(AppRegistry(["fake_event_app"]).load())

    assert event_registry.has_event_type("example.created", 1) is True
    assert event_registry.to_dict() == {
        "handlers": [],
        "schemas": [
            {
                "event_type": "example.created",
                "event_version": 1,
                "required_payload_fields": ["resource_id"],
                "field_types": {"resource_id": "str"},
                "compatible_with": [],
            }
        ],
    }


def test_event_registry_rejects_duplicate_handler_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handle_created(envelope: EventEnvelope) -> None:
        return None

    _install_handler_module(monkeypatch, handle_created=handle_created)
    duplicate_spec = EventHandlerSpec(
        event_type="example.created",
        event_version=1,
        handler_path="fake_event_handlers.handle_created",
    )
    _install_app(
        monkeypatch,
        event_handlers=[duplicate_spec, duplicate_spec],
    )

    with pytest.raises(ValueError, match="Duplicate event handler"):
        EventRegistry.from_app_registry(AppRegistry(["fake_event_app"]).load())


def test_event_registry_rejects_duplicate_direct_handler_key() -> None:
    registry = EventRegistry()

    def handle_created(envelope: EventEnvelope) -> None:
        return None

    registry.register("example.created", 1, handle_created)

    with pytest.raises(ValueError, match="Duplicate event handler"):
        registry.register("example.created", 1, handle_created)


def test_event_registry_rejects_non_callable_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_handler_module(monkeypatch, handle_created="not-callable")
    _install_app(
        monkeypatch,
        event_handlers=[
            EventHandlerSpec(
                event_type="example.created",
                event_version=1,
                handler_path="fake_event_handlers.handle_created",
            )
        ],
    )

    with pytest.raises(TypeError, match="must be callable"):
        EventRegistry.from_app_registry(AppRegistry(["fake_event_app"]).load())


def test_event_registry_validates_payload_schema_and_version_compatibility() -> None:
    registry = EventRegistry()
    registry.register_schema(
        EventSchemaSpec(
            event_type="example.changed",
            event_version=1,
            required_payload_fields=["resource_id"],
            field_types={
                "tenant_id": "str",
                "actor_id": "str",
                "request_id": "str",
                "resource_id": "str",
                "sequence": "int",
            },
        )
    )
    registry.register_schema(
        EventSchemaSpec(
            event_type="example.changed",
            event_version=2,
            required_payload_fields=["resource_id"],
            field_types={
                "tenant_id": "str",
                "actor_id": "str",
                "request_id": "str",
                "resource_id": "str",
                "sequence": "int",
                "metadata": "dict",
            },
            compatible_with=[1],
        )
    )

    registry.validate_event(
        event_type="example.changed",
        event_version=2,
        tenant_id="tenant-a",
        payload={
            "tenant_id": "tenant-a",
            "actor_id": "user-1",
            "request_id": "req-1",
            "resource_id": "resource-1",
            "sequence": 1,
            "metadata": {"source": "test"},
        },
    )

    with pytest.raises(EventPayloadValidationError, match="resource_id"):
        registry.validate_event(
            event_type="example.changed",
            event_version=2,
            tenant_id="tenant-a",
            payload={
                "tenant_id": "tenant-a",
                "actor_id": "user-1",
                "request_id": "req-1",
            },
        )
    with pytest.raises(EventPayloadValidationError, match="sequence.*int"):
        registry.validate_event(
            event_type="example.changed",
            event_version=2,
            tenant_id="tenant-a",
            payload={
                "tenant_id": "tenant-a",
                "actor_id": "user-1",
                "request_id": "req-1",
                "resource_id": "resource-1",
                "sequence": "wrong",
            },
        )
    with pytest.raises(EventSchemaCompatibilityError, match="missing compatible version"):
        registry.register_schema(
            EventSchemaSpec(
                event_type="example.changed",
                event_version=3,
                compatible_with=[99],
            )
        )
    with pytest.raises(EventSchemaCompatibilityError, match="removes required fields"):
        registry.register_schema(
            EventSchemaSpec(
                event_type="example.changed",
                event_version=3,
                required_payload_fields=[],
                field_types={"resource_id": "str"},
                compatible_with=[1],
            )
        )
    with pytest.raises(EventSchemaCompatibilityError, match="changes field type"):
        registry.register_schema(
            EventSchemaSpec(
                event_type="example.changed",
                event_version=4,
                required_payload_fields=["resource_id"],
                field_types={"resource_id": "int"},
                compatible_with=[1],
            )
        )


def _install_handler_module(
    monkeypatch: pytest.MonkeyPatch,
    **handlers,
) -> None:
    handler_module = types.ModuleType("fake_event_handlers")
    for name, handler in handlers.items():
        setattr(handler_module, name, handler)
    monkeypatch.setitem(sys.modules, "fake_event_handlers", handler_module)


def _install_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    event_handlers: list[EventHandlerSpec],
    event_schemas: list[EventSchemaSpec] | None = None,
) -> None:
    app = types.ModuleType("fake_event_app")
    app.module = AppModule(
        label="event_app",
        version="0.1.0",
        event_schemas=event_schemas or [],
        event_handlers=event_handlers,
    )
    monkeypatch.setitem(sys.modules, "fake_event_app", app)


def _envelope(*, event_id: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_type="example.created",
        event_version=1,
        tenant_id="tenant-a",
        aggregate_type="example",
        aggregate_id="example-1",
        payload={"tenant_id": "tenant-a", "actor_id": "user-1", "request_id": "req-1"},
    )
