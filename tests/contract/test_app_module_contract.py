import sys
import types

import pytest

from core.apps import AppModule, AppRegistry, validate_app_module
from core.base import create_router
from core.permissions import PermissionSpec


def test_valid_app_module_contract() -> None:
    module = AppModule(
        label="example_domain",
        version="0.1.0",
        routers=[create_router("/examples")],
        models=["apps.example_domain.models"],
        permissions=[PermissionSpec(resource="example", action="read")],
    )

    assert validate_app_module(module) is module


def test_invalid_label_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid app label"):
        validate_app_module(AppModule(label="Invalid-Name", version="0.1.0"))


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


def test_circular_dependency_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    first = types.ModuleType("fake_app_alpha")
    first.module = AppModule(label="alpha", version="0.1.0", dependencies=["beta"])
    second = types.ModuleType("fake_app_beta")
    second.module = AppModule(label="beta", version="0.1.0", dependencies=["alpha"])
    monkeypatch.setitem(sys.modules, "fake_app_alpha", first)
    monkeypatch.setitem(sys.modules, "fake_app_beta", second)

    with pytest.raises(ValueError, match="Circular app dependency detected"):
        AppRegistry(["fake_app_alpha", "fake_app_beta"]).load()
