import sys
import types

import pytest

from core.admin import (
    AdminDashboardWidgetSpec,
    AdminModelSpec,
    AdminPermissionSpec,
    AdminRegistry,
    AdminRouteSpec,
)
from core.apps import AppModule, AppRegistry, validate_app_module


def test_admin_registry_collects_specs_from_app_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    audit_module = types.ModuleType("fake_audit_app")
    audit_module.module = AppModule(
        label="audit",
        version="0.1.0",
        admin_permissions=[
            AdminPermissionSpec(resource="admin_console", action="read"),
        ],
        admin_models=[
            AdminModelSpec(
                admin_id="audit.logs",
                model_path="platform_apps.audit.models.AuditLog",
                label="Audit Logs",
                permissions=[
                    AdminPermissionSpec(
                        resource="audit_logs",
                        action="read",
                        description="Read audit logs",
                    )
                ],
                tenant_scoped=False,
                read_only=True,
            )
        ],
        admin_routes=[
            AdminRouteSpec(
                route_id="audit.export",
                path="/admin/audit/export",
                methods=("POST",),
                handler_path="platform_apps.audit.admin.export",
                permissions=[
                    AdminPermissionSpec(resource="audit_exports", action="create"),
                ],
            )
        ],
        dashboard_widgets=[
            AdminDashboardWidgetSpec(
                widget_id="audit.recent_denials",
                title="Recent Denials",
                provider_path="platform_apps.audit.admin.recent_denials",
                permissions=[
                    AdminPermissionSpec(resource="audit_dashboard", action="read"),
                ],
            )
        ],
    )
    monkeypatch.setitem(sys.modules, "fake_audit_app", audit_module)
    app_registry = AppRegistry(["fake_audit_app"]).load()

    registry = AdminRegistry.from_app_registry(app_registry)

    assert [item.app_label for item in registry.model_admins] == ["audit"]
    assert registry.model_admins[0].spec.admin_id == "audit.logs"
    assert registry.admin_routes[0].spec.path == "/admin/audit/export"
    assert registry.dashboard_widgets[0].spec.widget_id == "audit.recent_denials"
    assert [(p.resource, p.action, p.scope) for p in registry.permission_specs()] == [
        ("admin:admin_console", "read", "platform"),
        ("admin:audit_logs", "read", "platform"),
        ("admin:audit_exports", "create", "platform"),
        ("admin:audit_dashboard", "read", "platform"),
    ]
    assert registry.to_dict() == {
        "admin_permissions": [
            {
                "app_label": "audit",
                "resource": "admin_console",
                "action": "read",
                "description": "",
                "risk_level": "high",
            },
            {
                "app_label": "audit",
                "resource": "audit_logs",
                "action": "read",
                "description": "Read audit logs",
                "risk_level": "high",
            },
            {
                "app_label": "audit",
                "resource": "audit_exports",
                "action": "create",
                "description": "",
                "risk_level": "high",
            },
            {
                "app_label": "audit",
                "resource": "audit_dashboard",
                "action": "read",
                "description": "",
                "risk_level": "high",
            },
        ],
        "model_admins": [
            {
                "app_label": "audit",
                "admin_id": "audit.logs",
                "model_path": "platform_apps.audit.models.AuditLog",
                "label": "Audit Logs",
                "tenant_scoped": False,
                "read_only": True,
                "permissions": [{"resource": "audit_logs", "action": "read"}],
            }
        ],
        "admin_routes": [
            {
                "app_label": "audit",
                "route_id": "audit.export",
                "path": "/admin/audit/export",
                "methods": ["POST"],
                "handler_path": "platform_apps.audit.admin.export",
                "permissions": [{"resource": "audit_exports", "action": "create"}],
            }
        ],
        "dashboard_widgets": [
            {
                "app_label": "audit",
                "widget_id": "audit.recent_denials",
                "title": "Recent Denials",
                "provider_path": "platform_apps.audit.admin.recent_denials",
                "permissions": [{"resource": "audit_dashboard", "action": "read"}],
            }
        ],
    }


def test_admin_registry_rejects_duplicate_ids_and_deduplicates_permissions() -> None:
    permission = AdminPermissionSpec(resource="users", action="read")
    registry = AdminRegistry()
    registry.register(
        "accounts",
        admin_models=[
            AdminModelSpec(
                admin_id="users",
                model_path="platform_apps.accounts.models.User",
                label="Users",
                permissions=[permission],
            )
        ],
    )

    with pytest.raises(ValueError, match="Duplicate admin model"):
        registry.register(
            "accounts",
            admin_models=[
                AdminModelSpec(
                    admin_id="users",
                    model_path="platform_apps.accounts.models.User",
                    label="Users",
                    permissions=[AdminPermissionSpec(resource="users", action="write")],
                )
            ],
        )

    registry.register("accounts", admin_permissions=[permission])

    assert [(p.resource, p.action) for p in registry.permission_specs()] == [
        ("admin:users", "read"),
    ]

    with pytest.raises(ValueError, match="Conflicting admin permission"):
        registry.register(
            "accounts",
            admin_permissions=[
                AdminPermissionSpec(
                    resource="users",
                    action="read",
                    description="Different metadata",
                )
            ],
        )


def test_admin_registry_rejects_duplicate_route_and_widget_ids() -> None:
    registry = AdminRegistry()
    registry.register(
        "accounts",
        admin_routes=[
            AdminRouteSpec(
                route_id="users",
                path="/admin/users",
                handler_path="platform_apps.accounts.admin.users",
                permissions=[AdminPermissionSpec(resource="user_routes", action="read")],
            )
        ],
        dashboard_widgets=[
            AdminDashboardWidgetSpec(
                widget_id="users",
                title="Users",
                provider_path="platform_apps.accounts.admin.users_widget",
                permissions=[AdminPermissionSpec(resource="user_widgets", action="read")],
            )
        ],
    )

    with pytest.raises(ValueError, match="Duplicate admin route"):
        registry.register(
            "accounts",
            admin_routes=[
                AdminRouteSpec(
                    route_id="users",
                    path="/admin/users/export",
                    handler_path="platform_apps.accounts.admin.users_export",
                    permissions=[AdminPermissionSpec(resource="user_routes", action="write")],
                )
            ],
        )

    with pytest.raises(ValueError, match="Duplicate dashboard widget"):
        registry.register(
            "accounts",
            dashboard_widgets=[
                AdminDashboardWidgetSpec(
                    widget_id="users",
                    title="Users Duplicate",
                    provider_path="platform_apps.accounts.admin.users_widget_duplicate",
                    permissions=[AdminPermissionSpec(resource="user_widgets", action="write")],
                )
            ],
        )


def test_admin_specs_validate_routes_methods_and_platform_boundaries() -> None:
    with pytest.raises(ValueError, match="path must start with /admin"):
        AdminRouteSpec(
            route_id="bad.route",
            path="/api/v1/not-admin",
            handler_path="apps.example.admin.handler",
            permissions=[AdminPermissionSpec(resource="bad", action="read")],
        )
    with pytest.raises(ValueError, match="path must start with /admin"):
        AdminRouteSpec(
            route_id="bad.prefix",
            path="/administrator/users",
            handler_path="apps.example.admin.handler",
            permissions=[AdminPermissionSpec(resource="bad", action="read")],
        )
    with pytest.raises(ValueError, match="unsupported method"):
        AdminRouteSpec(
            route_id="bad.method",
            path="/admin/bad",
            methods=("TRACE",),
            handler_path="apps.example.admin.handler",
            permissions=[AdminPermissionSpec(resource="bad", action="read")],
        )
    with pytest.raises(ValueError, match="resource must not start with admin:"):
        AdminPermissionSpec(resource="admin:users", action="read")


def test_app_module_validates_admin_spec_types() -> None:
    module = AppModule(
        label="accounts",
        version="0.1.0",
        admin_permissions=[AdminPermissionSpec(resource="users", action="read")],
    )

    assert validate_app_module(module) is module

    with pytest.raises(TypeError, match="admin_model must be AdminModelSpec"):
        validate_app_module(
            AppModule(
                label="bad",
                version="0.1.0",
                admin_models=["not-a-spec"],  # type: ignore[list-item]
            )
        )
    with pytest.raises(TypeError, match="admin_route must be AdminRouteSpec"):
        validate_app_module(
            AppModule(
                label="bad",
                version="0.1.0",
                admin_routes=["not-a-spec"],  # type: ignore[list-item]
            )
        )
    with pytest.raises(TypeError, match="dashboard_widget must be AdminDashboardWidgetSpec"):
        validate_app_module(
            AppModule(
                label="bad",
                version="0.1.0",
                dashboard_widgets=["not-a-spec"],  # type: ignore[list-item]
            )
        )
    with pytest.raises(TypeError, match="admin_permission must be AdminPermissionSpec"):
        validate_app_module(
            AppModule(
                label="bad",
                version="0.1.0",
                admin_permissions=["not-a-spec"],  # type: ignore[list-item]
            )
        )
