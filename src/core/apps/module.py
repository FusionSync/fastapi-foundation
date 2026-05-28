from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter

from core.permissions.specs import PermissionSpec

_LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class MigrationSpec:
    path: str
    depends_on: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AppModule:
    label: str
    version: str
    dependencies: list[str] = field(default_factory=list)
    routers: list[APIRouter] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    migrations: MigrationSpec | None = None
    permissions: list[PermissionSpec] = field(default_factory=list)
    event_handlers: list[Any] = field(default_factory=list)
    task_handlers: list[Any] = field(default_factory=list)
    schedules: list[Any] = field(default_factory=list)
    public_api: list[str] = field(default_factory=list)


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
    for public_api in module.public_api:
        if not isinstance(public_api, str) or not public_api:
            raise TypeError(f"App {module.label!r} public_api path must be a non-empty string")
    if module.migrations is not None and not isinstance(module.migrations, MigrationSpec):
        raise TypeError(f"App {module.label!r} migrations must be MigrationSpec")
    return module
