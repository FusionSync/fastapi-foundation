import sys
import types

import pytest

from core.apps import (
    AppModule,
    AppRegistry,
    EventHandlerSpec,
    ScheduleSpec,
    TaskHandlerSpec,
    validate_app_module,
)
from core.base import create_router
from core.permissions import PermissionSpec


def test_valid_app_module_contract() -> None:
    module = AppModule(
        label="example_domain",
        version="0.1.0",
        routers=[create_router("/examples")],
        models=["apps.example_domain.models"],
        permissions=[PermissionSpec(resource="example", action="read")],
        event_handlers=[
            EventHandlerSpec(
                event_type="example.created",
                event_version=1,
                handler_path="apps.example_domain.events.handle_example_created",
            )
        ],
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="apps.example_domain.tasks.refresh_example",
            )
        ],
        schedules=[
            ScheduleSpec(
                schedule_id="example.refresh.daily",
                task_type="example.refresh",
                trigger="cron",
                trigger_config={"hour": "1"},
            )
        ],
    )

    assert validate_app_module(module) is module


def test_invalid_label_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid app label"):
        validate_app_module(AppModule(label="Invalid-Name", version="0.1.0"))


def test_invalid_event_task_and_schedule_specs_are_rejected() -> None:
    with pytest.raises(TypeError, match="event_handler must be EventHandlerSpec"):
        validate_app_module(
            AppModule(
                label="demo",
                version="0.1.0",
                event_handlers=["not-a-spec"],  # type: ignore[list-item]
            )
        )
    with pytest.raises(ValueError, match="version must be positive"):
        validate_app_module(
            AppModule(
                label="demo",
                version="0.1.0",
                event_handlers=[
                    EventHandlerSpec(
                        event_type="demo.changed",
                        event_version=0,
                        handler_path="apps.demo.events.handle",
                    )
                ],
            )
        )
    with pytest.raises(TypeError, match="task_handler handler_path"):
        validate_app_module(
            AppModule(
                label="demo",
                version="0.1.0",
                task_handlers=[TaskHandlerSpec(task_type="demo.task", handler_path="")],
            )
        )
    with pytest.raises(TypeError, match="schedule must be ScheduleSpec"):
        validate_app_module(
            AppModule(
                label="demo",
                version="0.1.0",
                schedules=["not-a-spec"],  # type: ignore[list-item]
            )
        )


def test_duplicate_labels_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    first = types.ModuleType("fake_app_one")
    first.module = AppModule(label="demo", version="0.1.0")
    second = types.ModuleType("fake_app_two")
    second.module = AppModule(label="demo", version="0.1.0")
    monkeypatch.setitem(sys.modules, "fake_app_one", first)
    monkeypatch.setitem(sys.modules, "fake_app_two", second)

    with pytest.raises(ValueError, match="Duplicate app label"):
        AppRegistry(["fake_app_one", "fake_app_two"]).load()


def test_missing_dependency_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("fake_app_with_dependency")
    fake.module = AppModule(label="demo", version="0.1.0", dependencies=["missing"])
    monkeypatch.setitem(sys.modules, "fake_app_with_dependency", fake)

    with pytest.raises(ValueError, match="missing dependencies"):
        AppRegistry(["fake_app_with_dependency"]).load()


def test_registry_loads_modules_in_dependency_order(monkeypatch: pytest.MonkeyPatch) -> None:
    consumer = types.ModuleType("fake_app_consumer")
    consumer.module = AppModule(
        label="consumer",
        version="0.1.0",
        dependencies=["provider"],
    )
    provider = types.ModuleType("fake_app_provider")
    provider.module = AppModule(label="provider", version="0.1.0")
    monkeypatch.setitem(sys.modules, "fake_app_consumer", consumer)
    monkeypatch.setitem(sys.modules, "fake_app_provider", provider)

    registry = AppRegistry(["fake_app_consumer", "fake_app_provider"]).load()

    assert [module.label for module in registry.modules] == ["provider", "consumer"]


def test_circular_dependency_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    first = types.ModuleType("fake_app_alpha")
    first.module = AppModule(label="alpha", version="0.1.0", dependencies=["beta"])
    second = types.ModuleType("fake_app_beta")
    second.module = AppModule(label="beta", version="0.1.0", dependencies=["alpha"])
    monkeypatch.setitem(sys.modules, "fake_app_alpha", first)
    monkeypatch.setitem(sys.modules, "fake_app_beta", second)

    with pytest.raises(ValueError, match="circular dependencies: alpha -> beta -> alpha"):
        AppRegistry(["fake_app_alpha", "fake_app_beta"]).load()
