from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.apps import SettingSpec

if TYPE_CHECKING:
    from core.apps import AppRegistry


@dataclass(frozen=True, slots=True)
class RegisteredSetting:
    app_label: str
    spec: SettingSpec

    @property
    def key(self) -> tuple[str, str]:
        return (self.spec.module, self.spec.key)

    def to_dict(self) -> dict[str, object]:
        return {
            "app_label": self.app_label,
            "module": self.spec.module,
            "key": self.spec.key,
            "value_type": self.spec.value_type,
            "default": self.spec.default,
            "scopes": list(self.spec.scopes),
            "category": self.spec.category,
            "description": self.spec.description,
            "required": self.spec.required,
            "runtime_mutable": self.spec.runtime_mutable,
            "sensitive": self.spec.sensitive,
            "secret_ref_only": self.spec.secret_ref_only,
            "risk_level": self.spec.risk_level,
            "cache_ttl_seconds": self.spec.cache_ttl_seconds,
            "allowed_values": list(self.spec.allowed_values),
            "kind": self.spec.kind,
            "deprecated": self.spec.deprecated,
        }


@dataclass(slots=True)
class SettingRegistry:
    settings: list[RegisteredSetting] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_app_registry(cls, app_registry: AppRegistry) -> SettingRegistry:
        registry = cls()
        seen: dict[tuple[str, str], str] = {}
        for app_module in app_registry.modules:
            for setting in app_module.settings:
                registered = RegisteredSetting(app_module.label, setting)
                registry._register_setting(registered, seen)
        return registry

    def has_setting(self, *, module: str, key: str) -> bool:
        return any(
            setting.spec.module == module and setting.spec.key == key
            for setting in self.settings
        )

    def get(self, module: str, key: str) -> SettingSpec:
        for setting in self.settings:
            if setting.spec.module == module and setting.spec.key == key:
                return setting.spec
        raise KeyError(f"Unknown setting: {module}.{key}")

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": not self.errors,
            "errors": self.errors,
            "settings": [setting.to_dict() for setting in self.settings],
        }

    def _register_setting(
        self,
        registered: RegisteredSetting,
        seen: dict[tuple[str, str], str],
    ) -> None:
        if registered.key in seen:
            self.errors.append(
                "Duplicate setting "
                f"{registered.key!r} declared by {seen[registered.key]!r} "
                f"and {registered.app_label!r}"
            )
        seen[registered.key] = registered.app_label
        self.settings.append(registered)
