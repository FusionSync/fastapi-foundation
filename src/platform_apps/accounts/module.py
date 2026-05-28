from core.apps import AppModule, MigrationSpec
from platform_apps.accounts.permissions import PERMISSIONS
from platform_apps.accounts.router import router

module = AppModule(
    label="platform_accounts",
    version="0.1.0",
    routers=[router],
    models=["platform_apps.accounts.models"],
    migrations=MigrationSpec(path="platform_apps.accounts.migrations"),
    permissions=PERMISSIONS,
    public_api=["platform_apps.accounts.public_api"],
)
