from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from core.permissions.specs import PermissionSpec, RiskLevel

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_SUPPORTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
AdminRouteMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


@dataclass(frozen=True, slots=True)
class AdminPermissionSpec:
    resource: str
    action: str
    description: str = ""
    risk_level: RiskLevel = "high"

    def __post_init__(self) -> None:
        if self.resource.startswith("admin:"):
            raise ValueError("admin permission resource must not start with admin:")
        _validate_identifier(self.resource, "admin permission resource")
        _validate_identifier(self.action, "admin permission action")

    def to_permission_spec(self) -> PermissionSpec:
        return PermissionSpec(
            resource=f"admin:{self.resource}",
            action=self.action,
            scope="platform",
            description=self.description,
            risk_level=self.risk_level,
        )


@dataclass(frozen=True, slots=True)
class AdminModelSpec:
    admin_id: str
    model_path: str
    label: str
    permissions: list[AdminPermissionSpec]
    tenant_scoped: bool = True
    read_only: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.admin_id, "admin model id")
        _validate_path(self.model_path, "admin model path")
        _validate_label(self.label, "admin model label")
        _validate_permissions(self.permissions)


@dataclass(frozen=True, slots=True)
class AdminRouteSpec:
    route_id: str
    path: str
    handler_path: str
    permissions: list[AdminPermissionSpec]
    methods: tuple[AdminRouteMethod, ...] = ("GET",)

    def __post_init__(self) -> None:
        _validate_identifier(self.route_id, "admin route id")
        if self.path != "/admin" and not self.path.startswith("/admin/"):
            raise ValueError("admin route path must start with /admin")
        _validate_path(self.handler_path, "admin route handler_path")
        _validate_permissions(self.permissions)
        unsupported = [method for method in self.methods if method not in _SUPPORTED_METHODS]
        if unsupported:
            raise ValueError(f"admin route unsupported method: {unsupported[0]}")


@dataclass(frozen=True, slots=True)
class AdminDashboardWidgetSpec:
    widget_id: str
    title: str
    provider_path: str
    permissions: list[AdminPermissionSpec]

    def __post_init__(self) -> None:
        _validate_identifier(self.widget_id, "dashboard widget id")
        _validate_label(self.title, "dashboard widget title")
        _validate_path(self.provider_path, "dashboard widget provider_path")
        _validate_permissions(self.permissions)


def _validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_PATTERN.match(value):
        raise ValueError(f"{label} is invalid: {value!r}")


def _validate_path(value: str, label: str) -> None:
    if not isinstance(value, str) or "." not in value or not value.strip():
        raise ValueError(f"{label} must be a dotted import path")


def _validate_label(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")


def _validate_permissions(permissions: Sequence[AdminPermissionSpec]) -> None:
    if not permissions:
        raise ValueError("admin specs must declare at least one permission")
    for permission in permissions:
        if not isinstance(permission, AdminPermissionSpec):
            raise TypeError("admin permission must be AdminPermissionSpec")
