from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter

from core.admin.specs import (
    AdminDashboardWidgetSpec,
    AdminModelSpec,
    AdminPermissionSpec,
    AdminRouteSpec,
)
from core.permissions.specs import PermissionSpec

_LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
ScheduleTrigger = Literal["interval", "cron", "date", "manual"]
MisfirePolicy = Literal["skip", "run_once", "catch_up_limited"]


@dataclass(frozen=True, slots=True)
class MigrationSpec:
    path: str
    depends_on: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EventHandlerSpec:
    event_type: str
    event_version: int
    handler_path: str


@dataclass(frozen=True, slots=True)
class TaskHandlerSpec:
    task_type: str
    handler_path: str
    queue: str = "default"


@dataclass(frozen=True, slots=True)
class ScheduleSpec:
    schedule_id: str
    task_type: str
    trigger: ScheduleTrigger
    trigger_config: dict[str, str] = field(default_factory=dict)
    misfire_policy: MisfirePolicy = "skip"


@dataclass(frozen=True, slots=True)
class AppModule:
    label: str
    version: str
    dependencies: list[str] = field(default_factory=list)
    routers: list[APIRouter] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    migrations: MigrationSpec | None = None
    permissions: list[PermissionSpec] = field(default_factory=list)
    event_handlers: list[EventHandlerSpec] = field(default_factory=list)
    task_handlers: list[TaskHandlerSpec] = field(default_factory=list)
    schedules: list[ScheduleSpec] = field(default_factory=list)
    public_api: list[str] = field(default_factory=list)
    admin_models: list[AdminModelSpec] = field(default_factory=list)
    admin_routes: list[AdminRouteSpec] = field(default_factory=list)
    dashboard_widgets: list[AdminDashboardWidgetSpec] = field(default_factory=list)
    admin_permissions: list[AdminPermissionSpec] = field(default_factory=list)


def validate_app_module(module: AppModule) -> AppModule:
    if not isinstance(module, AppModule):
        raise TypeError("module must be an AppModule instance")
    if not _LABEL_PATTERN.match(module.label):
        raise ValueError(f"Invalid app label: {module.label!r}")
    if not module.version:
        raise ValueError(f"App {module.label!r} must declare version")
    for dependency in module.dependencies:
        if not _LABEL_PATTERN.match(dependency):
            raise ValueError(f"App {module.label!r} has invalid dependency: {dependency!r}")
    if module.label in module.dependencies:
        raise ValueError(f"App {module.label!r} cannot depend on itself")
    for model in module.models:
        if not isinstance(model, str) or not model:
            raise TypeError(f"App {module.label!r} model path must be a non-empty string")
    for router in module.routers:
        if not isinstance(router, APIRouter):
            raise TypeError(f"App {module.label!r} router must be APIRouter")
    for permission in module.permissions:
        if not isinstance(permission, PermissionSpec):
            raise TypeError(f"App {module.label!r} permission must be PermissionSpec")
    for event_handler in module.event_handlers:
        if not isinstance(event_handler, EventHandlerSpec):
            raise TypeError(f"App {module.label!r} event_handler must be EventHandlerSpec")
        if event_handler.event_version < 1:
            raise ValueError(f"App {module.label!r} event_handler version must be positive")
        _validate_non_empty_path(
            event_handler.event_type,
            f"App {module.label!r} event_handler event_type",
        )
        _validate_non_empty_path(
            event_handler.handler_path,
            f"App {module.label!r} event_handler handler_path",
        )
    for task_handler in module.task_handlers:
        if not isinstance(task_handler, TaskHandlerSpec):
            raise TypeError(f"App {module.label!r} task_handler must be TaskHandlerSpec")
        _validate_non_empty_path(
            task_handler.task_type,
            f"App {module.label!r} task_handler task_type",
        )
        _validate_non_empty_path(
            task_handler.handler_path,
            f"App {module.label!r} task_handler handler_path",
        )
        _validate_non_empty_path(task_handler.queue, f"App {module.label!r} task_handler queue")
    for schedule in module.schedules:
        if not isinstance(schedule, ScheduleSpec):
            raise TypeError(f"App {module.label!r} schedule must be ScheduleSpec")
        _validate_non_empty_path(schedule.schedule_id, f"App {module.label!r} schedule_id")
        _validate_non_empty_path(schedule.task_type, f"App {module.label!r} schedule task_type")
    for public_api in module.public_api:
        if not isinstance(public_api, str) or not public_api:
            raise TypeError(f"App {module.label!r} public_api path must be a non-empty string")
    for admin_model in module.admin_models:
        if not isinstance(admin_model, AdminModelSpec):
            raise TypeError(f"App {module.label!r} admin_model must be AdminModelSpec")
    for admin_route in module.admin_routes:
        if not isinstance(admin_route, AdminRouteSpec):
            raise TypeError(f"App {module.label!r} admin_route must be AdminRouteSpec")
    for dashboard_widget in module.dashboard_widgets:
        if not isinstance(dashboard_widget, AdminDashboardWidgetSpec):
            raise TypeError(
                f"App {module.label!r} dashboard_widget must be AdminDashboardWidgetSpec"
            )
    for admin_permission in module.admin_permissions:
        if not isinstance(admin_permission, AdminPermissionSpec):
            raise TypeError(
                f"App {module.label!r} admin_permission must be AdminPermissionSpec"
            )
    if module.migrations is not None and not isinstance(module.migrations, MigrationSpec):
        raise TypeError(f"App {module.label!r} migrations must be MigrationSpec")
    return module


def _validate_non_empty_path(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{label} must be a non-empty string")
