from core.apps import AppRegistry
from core.apps.conformance import check_app
from core.migrations import MigrationRegistry
from core.permissions import PermissionRegistry

PLATFORM_APP_MODULES = [
    "platform_apps.accounts.module",
    "platform_apps.audit.module",
    "platform_apps.files.module",
]


def test_platform_apps_are_standard_app_modules() -> None:
    for module_path in PLATFORM_APP_MODULES:
        result = check_app(module_path)

        assert result.ok is True, result.errors


def test_platform_apps_register_permissions_and_migrations() -> None:
    app_registry = AppRegistry(PLATFORM_APP_MODULES).load()
    permission_registry = PermissionRegistry.from_app_registry(app_registry)
    migration_registry = MigrationRegistry.from_app_registry(app_registry)

    assert [module.label for module in app_registry.modules] == [
        "platform_accounts",
        "platform_audit",
        "platform_files",
    ]
    assert permission_registry.errors == []
    assert migration_registry.errors == []
    assert {
        (permission.app_label, permission.spec.resource, permission.spec.action)
        for permission in permission_registry.permissions
    } >= {
        ("platform_accounts", "user", "manage"),
        ("platform_audit", "audit_log", "read"),
        ("platform_files", "file", "download"),
    }
