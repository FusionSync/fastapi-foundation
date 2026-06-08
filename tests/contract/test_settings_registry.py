import sys
import types

import pytest

from core.apps import AppModule, AppRegistry, SettingSpec
from core.settings import SettingRegistry


def test_setting_registry_collects_app_module_setting_specs(monkeypatch) -> None:
    module = types.ModuleType("fake_setting_app")
    module.module = AppModule(
        label="fake_setting",
        version="0.1.0",
        settings=[
            SettingSpec(
                module="fake_setting",
                key="feature_enabled",
                value_type="bool",
                default=False,
                scopes=("platform", "tenant"),
                category="app_setting",
                description="Enable fake feature.",
            )
        ],
    )
    monkeypatch.setitem(sys.modules, "fake_setting_app", module)

    registry = SettingRegistry.from_app_registry(AppRegistry(["fake_setting_app"]).load())

    assert registry.errors == []
    assert registry.has_setting(module="fake_setting", key="feature_enabled")
    assert registry.get("fake_setting", "feature_enabled").default is False
    assert registry.to_dict()["settings"][0]["key"] == "feature_enabled"


def test_setting_registry_rejects_duplicate_settings(monkeypatch) -> None:
    first = types.ModuleType("first_setting_app")
    second = types.ModuleType("second_setting_app")
    spec = SettingSpec(
        module="shared",
        key="max_items",
        value_type="int",
        default=10,
        scopes=("platform",),
        category="platform_policy",
        description="Maximum items.",
    )
    first.module = AppModule(label="first_setting", version="0.1.0", settings=[spec])
    second.module = AppModule(label="second_setting", version="0.1.0", settings=[spec])
    monkeypatch.setitem(sys.modules, "first_setting_app", first)
    monkeypatch.setitem(sys.modules, "second_setting_app", second)

    registry = SettingRegistry.from_app_registry(
        AppRegistry(["first_setting_app", "second_setting_app"]).load()
    )

    assert registry.errors == [
        "Duplicate setting ('shared', 'max_items') declared by "
        "'first_setting' and 'second_setting'"
    ]


def test_setting_spec_validates_default_and_key() -> None:
    with pytest.raises(ValueError, match="key"):
        SettingSpec(
            module="fake",
            key="Bad Key",
            value_type="bool",
            default=False,
            scopes=("platform",),
            category="app_setting",
            description="Invalid key.",
        )

    with pytest.raises(ValueError, match="default"):
        SettingSpec(
            module="fake",
            key="enabled",
            value_type="bool",
            default="yes",
            scopes=("platform",),
            category="app_setting",
            description="Invalid default.",
        )
