from core.apps import AppModule, MigrationSpec
from platform_apps.audit.permissions import PERMISSIONS
from platform_apps.audit.router import (
    export_read_router,
    export_router,
    log_router,
    retention_router,
    verify_router,
)

module = AppModule(
    label="platform_audit",
    version="0.1.0",
    routers=[log_router, verify_router, export_read_router, export_router, retention_router],
    models=["platform_apps.audit.models"],
    migrations=MigrationSpec(path="platform_apps.audit.migrations"),
    permissions=PERMISSIONS,
    public_api=["platform_apps.audit.public_api"],
)
