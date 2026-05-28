from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from core.apps import AppRegistry, TaskHandlerSpec


@dataclass(frozen=True, slots=True)
class TaskEnvelope:
    task_id: str
    task_type: str
    tenant_id: str
    payload: dict[str, Any]
    idempotency_key: str
    request_id: str


TaskHandler = Callable[[TaskEnvelope], Awaitable[dict[str, Any] | None] | dict[str, Any] | None]


@dataclass(frozen=True, slots=True)
class RegisteredTaskHandler:
    app_label: str
    spec: TaskHandlerSpec
    handler: TaskHandler


class TaskRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, RegisteredTaskHandler] = {}

    @classmethod
    def from_app_registry(cls, app_registry: AppRegistry) -> TaskRegistry:
        registry = cls()
        for app_module in app_registry.modules:
            for spec in app_module.task_handlers:
                registry.register(app_module.label, spec)
        return registry

    @property
    def task_types(self) -> set[str]:
        return set(self._handlers)

    def register(self, app_label: str, spec: TaskHandlerSpec) -> None:
        if spec.task_type in self._handlers:
            raise ValueError(f"Duplicate task handler for {spec.task_type!r}")
        self._handlers[spec.task_type] = RegisteredTaskHandler(
            app_label=app_label,
            spec=spec,
            handler=_import_task_handler(spec.handler_path),
        )

    def has_task_type(self, task_type: str) -> bool:
        return task_type in self._handlers

    def get(self, task_type: str) -> RegisteredTaskHandler:
        try:
            return self._handlers[task_type]
        except KeyError as exc:
            raise ValueError(f"No task handler registered for {task_type!r}") from exc

    def to_dict(self) -> dict[str, object]:
        return {
            "tasks": [
                {
                    "app_label": registered.app_label,
                    "task_type": registered.spec.task_type,
                    "handler_path": registered.spec.handler_path,
                    "queue": registered.spec.queue,
                }
                for registered in self._handlers.values()
            ]
        }


def _import_task_handler(handler_path: str) -> TaskHandler:
    module_path, _, attribute = handler_path.rpartition(".")
    if not module_path or not attribute:
        raise ValueError(f"Invalid task handler path: {handler_path!r}")
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        raise ImportError(f"Failed to import task handler {handler_path!r}: {exc}") from exc
    handler = getattr(module, attribute, None)
    if not callable(handler):
        raise TypeError(f"Task handler {handler_path!r} must be callable")
    return handler
