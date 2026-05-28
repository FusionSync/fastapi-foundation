from __future__ import annotations

import importlib
from collections.abc import Iterable

from fastapi import APIRouter

from core.apps.module import AppModule, validate_app_module


class AppRegistry:
    def __init__(self, installed_apps: Iterable[str] = ()) -> None:
        self.installed_apps = list(installed_apps)
        self.modules: list[AppModule] = []

    def load(self) -> AppRegistry:
        loaded: list[AppModule] = []
        labels: set[str] = set()

        for module_path in self.installed_apps:
            try:
                imported = importlib.import_module(module_path)
            except Exception as exc:
                raise ImportError(f"Failed to import app module {module_path!r}: {exc}") from exc
            try:
                app_module = validate_app_module(imported.module)
            except AttributeError as exc:
                raise ValueError(f"App module {module_path!r} must expose `module`") from exc
            except Exception as exc:
                raise ValueError(f"Invalid app module {module_path!r}: {exc}") from exc
            if app_module.label in labels:
                raise ValueError(f"Duplicate app label: {app_module.label}")
            labels.add(app_module.label)
            loaded.append(app_module)

        self._validate_dependencies(loaded, labels)
        self.modules = loaded
        return self

    @property
    def routers(self) -> list[APIRouter]:
        return [router for module in self.modules for router in module.routers]

    def _validate_dependencies(self, modules: list[AppModule], labels: set[str]) -> None:
        for module in modules:
            missing = [dependency for dependency in module.dependencies if dependency not in labels]
            if missing:
                raise ValueError(f"App {module.label!r} has missing dependencies: {missing}")
        self._validate_no_cycles(modules)

    def _validate_no_cycles(self, modules: list[AppModule]) -> None:
        graph = {module.label: list(module.dependencies) for module in modules}
        visiting: set[str] = set()
        visited: set[str] = set()
        path: list[str] = []

        def visit(label: str) -> None:
            if label in visited:
                return
            if label in visiting:
                cycle_start = path.index(label)
                cycle = [*path[cycle_start:], label]
                raise ValueError(f"Circular app dependency detected: {' -> '.join(cycle)}")
            visiting.add(label)
            path.append(label)
            for dependency in graph[label]:
                visit(dependency)
            path.pop()
            visiting.remove(label)
            visited.add(label)

        for label in graph:
            visit(label)
