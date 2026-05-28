from core.apps import AppModule, MigrationSpec
from platform_apps.audit.permissions import PERMISSIONS
from platform_apps.audit.router import router

module = AppModule(
    label="platform_audit",
    version="0.1.0",
    routers=[router],
    models=["platform_apps.audit.models"],
    migrations=MigrationSpec(path="platform_apps.audit.migrations"),
    permissions=PERMISSIONS,
    public_api=["platform_apps.audit.public_api"],
)
