from core.apps import AppRegistry
from core.apps.conformance import check_app
from core.base import get_router_security_policy
from core.migrations import MigrationRegistry
from core.permissions import PermissionRegistry
from core.settings import SettingRegistry
from platform_apps.access import module as access_module
from platform_apps.settings import module as settings_module


def test_access_and_settings_platform_apps_are_standard_modules() -> None:
    for module_path in (
        "platform_apps.access.module",
        "platform_apps.settings.module",
    ):
        result = check_app(module_path)

        assert result.ok is True, result.errors


def test_access_and_settings_register_permissions_migrations_and_settings() -> None:
    app_registry = AppRegistry(
        [
            "platform_apps.access.module",
            "platform_apps.settings.module",
        ]
    ).load()
    permission_registry = PermissionRegistry.from_app_registry(app_registry)
    migration_registry = MigrationRegistry.from_app_registry(app_registry)
    setting_registry = SettingRegistry.from_app_registry(app_registry)

    assert [module.label for module in app_registry.modules] == [
        "platform_access",
        "platform_settings",
    ]
    assert permission_registry.errors == []
    assert migration_registry.errors == []
    assert setting_registry.errors == []
    assert {
        (permission.app_label, permission.spec.resource, permission.spec.action)
        for permission in permission_registry.permissions
    } >= {
        ("platform_access", "access.platform_admin", "manage"),
        ("platform_settings", "settings.value", "manage"),
    }
    assert setting_registry.has_setting(module="files", key="max_file_size_mb")


def test_access_and_settings_routes_declare_expected_security_policies() -> None:
    policies = {
        (
            router.prefix,
            get_router_security_policy(router).permissions,
            get_router_security_policy(router).tenant_required,
            get_router_security_policy(router).permission_scope,
        )
    for router in [*access_module.routers, *settings_module.routers]
        if get_router_security_policy(router) is not None
    }

    assert (
        "/platform/access/platform-admins",
        ("access.platform_admin:manage",),
        False,
        "platform",
    ) in policies
    assert (
        "/platform/settings/values",
        ("settings.value:manage",),
        False,
        "platform",
    ) in policies
