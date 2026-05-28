from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.admin.specs import (
    AdminDashboardWidgetSpec,
    AdminModelSpec,
    AdminPermissionSpec,
    AdminRouteSpec,
)
from core.permissions.specs import PermissionSpec

if TYPE_CHECKING:
    from core.apps import AppRegistry


@dataclass(frozen=True, slots=True)
class RegisteredModelAdmin:
    app_label: str
    spec: AdminModelSpec


@dataclass(frozen=True, slots=True)
class RegisteredRouteAdmin:
    app_label: str
    spec: AdminRouteSpec


@dataclass(frozen=True, slots=True)
class RegisteredDashboardWidget:
    app_label: str
    spec: AdminDashboardWidgetSpec


@dataclass(frozen=True, slots=True)
class RegisteredAdminPermission:
    app_label: str
    spec: AdminPermissionSpec


class AdminRegistry:
    def __init__(self) -> None:
        self.model_admins: list[RegisteredModelAdmin] = []
        self.admin_routes: list[RegisteredRouteAdmin] = []
        self.dashboard_widgets: list[RegisteredDashboardWidget] = []
        self.admin_permissions: list[RegisteredAdminPermission] = []
        self._model_ids: set[str] = set()
        self._route_ids: set[str] = set()
        self._widget_ids: set[str] = set()
        self._permissions: dict[tuple[str, str], RegisteredAdminPermission] = {}

    @classmethod
    def from_app_registry(cls, app_registry: AppRegistry) -> AdminRegistry:
        registry = cls()
        for module in app_registry.modules:
            registry.register(
                module.label,
                admin_models=module.admin_models,
                admin_routes=module.admin_routes,
                dashboard_widgets=module.dashboard_widgets,
                admin_permissions=module.admin_permissions,
            )
        return registry

    def register(
        self,
        app_label: str,
        *,
        admin_models: Sequence[AdminModelSpec] | None = None,
        admin_routes: Sequence[AdminRouteSpec] | None = None,
        dashboard_widgets: Sequence[AdminDashboardWidgetSpec] | None = None,
        admin_permissions: Sequence[AdminPermissionSpec] | None = None,
    ) -> None:
        for permission in admin_permissions or []:
            self._register_permission(app_label, permission)
        for model in admin_models or []:
            if model.admin_id in self._model_ids:
                raise ValueError(f"Duplicate admin model: {model.admin_id}")
            self._model_ids.add(model.admin_id)
            self.model_admins.append(RegisteredModelAdmin(app_label=app_label, spec=model))
            for permission in model.permissions:
                self._register_permission(app_label, permission)
        for route in admin_routes or []:
            if route.route_id in self._route_ids:
                raise ValueError(f"Duplicate admin route: {route.route_id}")
            self._route_ids.add(route.route_id)
            self.admin_routes.append(RegisteredRouteAdmin(app_label=app_label, spec=route))
            for permission in route.permissions:
                self._register_permission(app_label, permission)
        for widget in dashboard_widgets or []:
            if widget.widget_id in self._widget_ids:
                raise ValueError(f"Duplicate dashboard widget: {widget.widget_id}")
            self._widget_ids.add(widget.widget_id)
            self.dashboard_widgets.append(
                RegisteredDashboardWidget(app_label=app_label, spec=widget)
            )
            for permission in widget.permissions:
                self._register_permission(app_label, permission)

    def permission_specs(self) -> list[PermissionSpec]:
        return [
            permission.spec.to_permission_spec()
            for permission in self.admin_permissions
        ]

    def to_dict(self) -> dict[str, list[dict[str, object]]]:
        return {
            "admin_permissions": [
                {
                    "app_label": item.app_label,
                    "resource": item.spec.resource,
                    "action": item.spec.action,
                    "description": item.spec.description,
                    "risk_level": item.spec.risk_level,
                }
                for item in self.admin_permissions
            ],
            "model_admins": [
                {
                    "app_label": item.app_label,
                    "admin_id": item.spec.admin_id,
                    "model_path": item.spec.model_path,
                    "label": item.spec.label,
                    "tenant_scoped": item.spec.tenant_scoped,
                    "read_only": item.spec.read_only,
                    "permissions": _permissions_to_dict(item.spec.permissions),
                }
                for item in self.model_admins
            ],
            "admin_routes": [
                {
                    "app_label": item.app_label,
                    "route_id": item.spec.route_id,
                    "path": item.spec.path,
                    "methods": list(item.spec.methods),
                    "handler_path": item.spec.handler_path,
                    "permissions": _permissions_to_dict(item.spec.permissions),
                }
                for item in self.admin_routes
            ],
            "dashboard_widgets": [
                {
                    "app_label": item.app_label,
                    "widget_id": item.spec.widget_id,
                    "title": item.spec.title,
                    "provider_path": item.spec.provider_path,
                    "permissions": _permissions_to_dict(item.spec.permissions),
                }
                for item in self.dashboard_widgets
            ],
        }

    def _register_permission(self, app_label: str, permission: AdminPermissionSpec) -> None:
        if not isinstance(permission, AdminPermissionSpec):
            raise TypeError("admin_permission must be AdminPermissionSpec")
        key = (permission.resource, permission.action)
        existing = self._permissions.get(key)
        if existing is not None and existing.spec != permission:
            raise ValueError(
                f"Conflicting admin permission: {permission.resource}.{permission.action}"
            )
        if existing is not None:
            return
        registered = RegisteredAdminPermission(app_label=app_label, spec=permission)
        self._permissions[key] = registered
        self.admin_permissions.append(registered)


def _permissions_to_dict(permissions: Sequence[AdminPermissionSpec]) -> list[dict[str, str]]:
    return [
        {"resource": permission.resource, "action": permission.action}
        for permission in permissions
    ]
