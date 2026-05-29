from core.apps import AppRegistry
from core.apps.conformance import check_app
from core.base import get_router_security_policy
from core.migrations import MigrationRegistry
from core.permissions import PermissionRegistry
from platform_apps.tenants import module as tenants_module

PLATFORM_APP_MODULES = [
    "platform_apps.accounts.module",
    "platform_apps.audit.module",
    "platform_apps.files.module",
    "platform_apps.tenants.module",
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
        "platform_tenants",
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
        ("platform_tenants", "tenant", "manage"),
    }


def test_platform_tenant_routes_declare_security_policies() -> None:
    policies = {
        (
            router.prefix,
            get_router_security_policy(router).permissions,
            get_router_security_policy(router).tenant_required,
            get_router_security_policy(router).permission_scope,
        )
        for router in tenants_module.routers
        if get_router_security_policy(router) is not None
    }

    assert (
        "/platform/tenants",
        ("tenant:manage",),
        False,
        "platform",
    ) in policies
    assert (
        "/tenants/{tenant_id}/members",
        ("tenant_member:read",),
        True,
        "tenant",
    ) in policies
    assert (
        "/tenants/{tenant_id}/members",
        ("tenant_member:manage",),
        True,
        "tenant",
    ) in policies
    assert (
        "/tenants/{tenant_id}/invitations",
        ("tenant_invitation:invite",),
        True,
        "tenant",
    ) in policies
    assert (
        "/tenants/{tenant_id}/invitations",
        ("tenant_invitation:revoke",),
        True,
        "tenant",
    ) in policies
    assert (
        "/tenant-invitations",
        (),
        False,
        None,
    ) in policies
