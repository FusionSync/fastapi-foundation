from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.permissions.specs import PermissionSpec

if TYPE_CHECKING:
    from core.apps import AppRegistry


@dataclass(frozen=True, slots=True)
class RegisteredPermission:
    app_label: str
    spec: PermissionSpec

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.spec.scope, self.spec.resource, self.spec.action)

    def to_dict(self) -> dict[str, object]:
        return {
            "app_label": self.app_label,
            "resource": self.spec.resource,
            "action": self.spec.action,
            "scope": self.spec.scope,
            "description": self.spec.description,
            "risk_level": self.spec.risk_level,
        }


@dataclass(slots=True)
class PermissionRegistry:
    permissions: list[RegisteredPermission] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_app_registry(cls, app_registry: AppRegistry) -> PermissionRegistry:
        from core.admin.registry import AdminRegistry

        registry = cls()
        seen: dict[tuple[str, str, str], str] = {}
        for app_module in app_registry.modules:
            for permission in app_module.permissions:
                registered = RegisteredPermission(app_module.label, permission)
                registry._register_permission(registered, seen)
        try:
            admin_registry = AdminRegistry.from_app_registry(app_registry)
        except ValueError as exc:
            registry.errors.append(str(exc))
        else:
            for admin_permission in admin_registry.admin_permissions:
                registered = RegisteredPermission(
                    admin_permission.app_label,
                    admin_permission.spec.to_permission_spec(),
                )
                registry._register_permission(registered, seen)
        return registry

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": not self.errors,
            "errors": self.errors,
            "permissions": [permission.to_dict() for permission in self.permissions],
        }

    def has_permission(self, *, resource: str, action: str, scope: str | None = None) -> bool:
        return any(
            permission.spec.resource == resource
            and permission.spec.action == action
            and (scope is None or permission.spec.scope == scope)
            for permission in self.permissions
        )

    def _register_permission(
        self,
        registered: RegisteredPermission,
        seen: dict[tuple[str, str, str], str],
    ) -> None:
        if registered.key in seen:
            self.errors.append(
                "Duplicate permission "
                f"{registered.key!r} declared by {seen[registered.key]!r} "
                f"and {registered.app_label!r}"
            )
        seen[registered.key] = registered.app_label
        self.permissions.append(registered)
