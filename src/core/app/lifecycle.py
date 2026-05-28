from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI

from core.apps import AppRegistry
from core.config import Settings

LifecyclePhase = Literal["startup", "shutdown"]


@dataclass(frozen=True, slots=True)
class AppLifecycleContext:
    app: FastAPI
    app_registry: AppRegistry
    settings: Settings
    app_label: str
    hook_id: str
    phase: LifecyclePhase


async def run_lifecycle_hooks(app: FastAPI, *, phase: LifecyclePhase) -> None:
    registry: AppRegistry = app.state.app_registry
    modules = list(registry.modules)
    if phase == "shutdown":
        modules = list(reversed(modules))
    for app_module in modules:
        hooks = [
            hook for hook in app_module.lifecycle_hooks if hook.phase == phase
        ]
        if phase == "shutdown":
            hooks = list(reversed(hooks))
        for hook in hooks:
            context = AppLifecycleContext(
                app=app,
                app_registry=registry,
                settings=app.state.settings,
                app_label=app_module.label,
                hook_id=hook.hook_id,
                phase=phase,
            )
            try:
                result = _load_handler(hook.handler_path)(context)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                raise RuntimeError(
                    f"{phase} lifecycle hook {hook.hook_id} failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc


def _load_handler(handler_path: str):
    module_path, separator, attribute = handler_path.rpartition(".")
    if not separator or not module_path or not attribute:
        raise ValueError(f"Invalid lifecycle handler path: {handler_path!r}")
    module = importlib.import_module(module_path)
    handler = getattr(module, attribute, None)
    if not callable(handler):
        raise TypeError(f"{handler_path!r} must be callable")
    return handler
