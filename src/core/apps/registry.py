from __future__ import annotations

import importlib
from collections.abc import Iterable

from fastapi import APIRouter

from core.apps.dependencies import validate_app_dependencies
from core.apps.module import AppModule, validate_app_module


class AppRegistry:
    def __init__(self, installed_apps: Iterable[str] = ()) -> None:
        self.installed_apps = list(installed_apps)
        self.modules: list[AppModule] = []

    def load(self) -> AppRegistry:
        loaded: list[AppModule] = []

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
            loaded.append(app_module)

        validation = validate_app_dependencies(loaded)
        if not validation.ok:
            raise ValueError("; ".join(validation.errors))
        self.modules = validation.ordered_modules
        return self

    @property
    def routers(self) -> list[APIRouter]:
        return [router for module in self.modules for router in module.routers]
