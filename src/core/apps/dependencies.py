from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from core.apps.module import AppModule


@dataclass(slots=True)
class AppDependencyValidation:
    ordered_modules: list[AppModule] = field(default_factory=list)
    errors_by_label: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors_by_label

    @property
    def errors(self) -> list[str]:
        return [error for errors in self.errors_by_label.values() for error in errors]


def validate_app_dependencies(modules: Sequence[AppModule]) -> AppDependencyValidation:
    modules_by_label: dict[str, AppModule] = {}
    errors_by_label: dict[str, list[str]] = {}
    ordered_labels = [module.label for module in modules]

    for module in modules:
        if module.label in modules_by_label:
            _add_error(
                errors_by_label,
                module.label,
                f"Duplicate app label: {module.label}",
            )
            continue
        modules_by_label[module.label] = module

    for module in modules_by_label.values():
        missing = [
            dependency
            for dependency in module.dependencies
            if dependency not in modules_by_label
        ]
        if missing:
            _add_error(
                errors_by_label,
                module.label,
                f"missing dependencies: {missing}",
            )

    ordered_modules: list[AppModule] = []
    if not errors_by_label:
        ordered_modules = [
            modules_by_label[label]
            for label in _topological_order(ordered_labels, modules_by_label, errors_by_label)
        ]

    return AppDependencyValidation(
        ordered_modules=ordered_modules,
        errors_by_label=errors_by_label,
    )


def _topological_order(
    labels: list[str],
    modules_by_label: dict[str, AppModule],
    errors_by_label: dict[str, list[str]],
) -> list[str]:
    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(label: str) -> None:
        if label in visited:
            return
        if label in visiting:
            cycle_start = path.index(label)
            cycle = [*path[cycle_start:], label]
            _add_error(
                errors_by_label,
                path[cycle_start],
                f"circular dependencies: {' -> '.join(cycle)}",
            )
            return
        visiting.add(label)
        path.append(label)
        for dependency in modules_by_label[label].dependencies:
            if dependency in modules_by_label:
                visit(dependency)
        path.pop()
        visiting.remove(label)
        visited.add(label)
        ordered.append(label)

    for label in labels:
        if label in modules_by_label:
            visit(label)

    if errors_by_label:
        return []
    return ordered


def _add_error(errors_by_label: dict[str, list[str]], label: str, error: str) -> None:
    errors_by_label.setdefault(label, []).append(error)
