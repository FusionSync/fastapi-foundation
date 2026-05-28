from __future__ import annotations

import importlib
from dataclasses import dataclass, field

from core.apps import AppModule, AppRegistry
from core.migrations.manifest import MigrationManifest


@dataclass(slots=True)
class MigrationRegistry:
    manifests: list[MigrationManifest] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_app_registry(cls, app_registry: AppRegistry) -> MigrationRegistry:
        registry = cls()
        for app_module in app_registry.modules:
            registry._collect_app_manifests(app_module)
        registry._validate_duplicates()
        return registry

    def _collect_app_manifests(self, app_module: AppModule) -> None:
        if app_module.migrations is None:
            self.errors.append(f"App {app_module.label!r} does not declare migrations")
            return
        manifest_module_path = f"{app_module.migrations.path}.manifest"
        try:
            manifest_module = importlib.import_module(manifest_module_path)
        except ModuleNotFoundError as exc:
            if exc.name == manifest_module_path:
                return
            self.errors.append(f"Failed to import {manifest_module_path}: {exc}")
            return
        raw_manifests = getattr(
            manifest_module,
            "MIGRATIONS",
            getattr(manifest_module, "migrations", []),
        )
        for raw_manifest in raw_manifests:
            if not isinstance(raw_manifest, MigrationManifest):
                self.errors.append(f"{manifest_module_path} contains non-MigrationManifest item")
                continue
            if raw_manifest.app_label != app_module.label:
                self.errors.append(
                    f"{raw_manifest.key} app_label does not match module label {app_module.label!r}"
                )
                continue
            self.manifests.append(raw_manifest)

    def _validate_duplicates(self) -> None:
        seen: set[str] = set()
        for manifest in self.manifests:
            if manifest.key in seen:
                self.errors.append(f"Duplicate migration manifest: {manifest.key}")
            seen.add(manifest.key)
