from core.apps import AppModule, MigrationSpec
from platform_apps.tenants.permissions import PERMISSIONS
from platform_apps.tenants.router import router

module = AppModule(
    label="platform_tenants",
    version="0.1.0",
    routers=[router],
    models=["platform_apps.tenants.models"],
    migrations=MigrationSpec(path="platform_apps.tenants.migrations"),
    permissions=PERMISSIONS,
    public_api=["platform_apps.tenants.public_api"],
)
