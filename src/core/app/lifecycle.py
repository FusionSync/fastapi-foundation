from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI

from core.apps import AppRegistry
from core.config import Settings

LifecyclePhase = Literal["startup", "shutdown"]
LifecycleStatus = Literal["succeeded", "failed"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AppLifecycleContext:
    app: FastAPI
    app_registry: AppRegistry
    settings: Settings
    app_label: str
    hook_id: str
    phase: LifecyclePhase


@dataclass(frozen=True, slots=True)
class LifecycleHookDiagnostic:
    app_label: str
    hook_id: str
    phase: LifecyclePhase
    handler_path: str
    status: LifecycleStatus
    error: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {
            "app_label": self.app_label,
            "hook_id": self.hook_id,
            "phase": self.phase,
            "handler_path": self.handler_path,
            "status": self.status,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


async def run_lifecycle_hooks(app: FastAPI, *, phase: LifecyclePhase) -> None:
    registry: AppRegistry = app.state.app_registry
    diagnostics = _ensure_lifecycle_diagnostics(app)
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
                diagnostic = LifecycleHookDiagnostic(
                    app_label=app_module.label,
                    hook_id=hook.hook_id,
                    phase=phase,
                    handler_path=hook.handler_path,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
                diagnostics[phase].append(diagnostic.to_dict())
                _log_lifecycle_hook(diagnostic)
                raise RuntimeError(
                    f"{phase} lifecycle hook {hook.hook_id} failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            diagnostic = LifecycleHookDiagnostic(
                app_label=app_module.label,
                hook_id=hook.hook_id,
                phase=phase,
                handler_path=hook.handler_path,
                status="succeeded",
            )
            diagnostics[phase].append(diagnostic.to_dict())
            _log_lifecycle_hook(diagnostic)


def _load_handler(handler_path: str):
    module_path, separator, attribute = handler_path.rpartition(".")
    if not separator or not module_path or not attribute:
        raise ValueError(f"Invalid lifecycle handler path: {handler_path!r}")
    module = importlib.import_module(module_path)
    handler = getattr(module, attribute, None)
    if not callable(handler):
        raise TypeError(f"{handler_path!r} must be callable")
    return handler


def _ensure_lifecycle_diagnostics(app: FastAPI) -> dict[LifecyclePhase, list[dict[str, str]]]:
    diagnostics = getattr(app.state, "lifecycle_diagnostics", None)
    if diagnostics is None:
        diagnostics = {"startup": [], "shutdown": []}
        app.state.lifecycle_diagnostics = diagnostics
    return diagnostics


def _log_lifecycle_hook(diagnostic: LifecycleHookDiagnostic) -> None:
    event_name = (
        "app_lifecycle_hook_succeeded"
        if diagnostic.status == "succeeded"
        else "app_lifecycle_hook_failed"
    )
    log = logger.info if diagnostic.status == "succeeded" else logger.error
    log(event_name, extra={"lifecycle_hook": diagnostic.to_dict()})
