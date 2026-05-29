from __future__ import annotations

from dataclasses import dataclass, field

from core.apps import AppRegistry
from core.migrations.manifest import MigrationManifest, MigrationPhase


@dataclass(slots=True)
class MigrationPlan:
    migrations: list[MigrationManifest] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    phase: MigrationPhase | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "migrations": [manifest.to_dict() for manifest in self.migrations],
            "errors": self.errors,
        }
        if self.phase is not None:
            payload["phase"] = self.phase
        return payload


def plan_migrations(
    manifests: list[MigrationManifest],
    *,
    app_registry: AppRegistry | None = None,
    phase: MigrationPhase | None = None,
) -> MigrationPlan:
    manifests_by_key = {manifest.key: manifest for manifest in manifests}
    errors: list[str] = []
    if len(manifests_by_key) != len(manifests):
        errors.append("Duplicate migration keys detected")

    graph: dict[str, set[str]] = {manifest.key: set() for manifest in manifests}
    manifests_by_app: dict[str, list[MigrationManifest]] = {}
    for manifest in manifests:
        manifests_by_app.setdefault(manifest.app_label, []).append(manifest)
        for dependency in manifest.depends_on:
            dependency_key = _dependency_key(manifest, dependency)
            if dependency_key not in manifests_by_key:
                errors.append(f"{manifest.key} depends on missing migration {dependency_key}")
            else:
                graph[manifest.key].add(dependency_key)

    if app_registry is not None:
        for app_module in app_registry.modules:
            for app_dependency in app_module.dependencies:
                for manifest in manifests_by_app.get(app_module.label, []):
                    for dependency_manifest in manifests_by_app.get(app_dependency, []):
                        graph[manifest.key].add(dependency_manifest.key)

    ordered_keys: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(key: str) -> None:
        if key in visited:
            return
        if key in visiting:
            cycle_start = path.index(key)
            cycle = " -> ".join([*path[cycle_start:], key])
            errors.append(f"Circular migration dependency: {cycle}")
            return
        visiting.add(key)
        path.append(key)
        for dependency in sorted(graph[key]):
            if dependency in graph:
                visit(dependency)
        path.pop()
        visiting.remove(key)
        visited.add(key)
        ordered_keys.append(key)

    for key in sorted(graph):
        visit(key)

    ordered_migrations = [
        manifests_by_key[key] for key in ordered_keys if key in manifests_by_key
    ]
    if phase is not None:
        ordered_migrations = [
            manifest for manifest in ordered_migrations if manifest.phase == phase
        ]

    return MigrationPlan(
        migrations=ordered_migrations,
        errors=errors,
        phase=phase,
    )


def _dependency_key(manifest: MigrationManifest, dependency: str) -> str:
    return dependency if ":" in dependency else f"{manifest.app_label}:{dependency}"
