from core.apps import AppModule, MigrationSpec
from platform_apps.files.permissions import PERMISSIONS
from platform_apps.files.router import delete_router, read_router, router

module = AppModule(
    label="platform_files",
    version="0.1.0",
    routers=[router, read_router, delete_router],
    models=["platform_apps.files.models"],
    migrations=MigrationSpec(path="platform_apps.files.migrations"),
    permissions=PERMISSIONS,
    public_api=["platform_apps.files.public_api"],
)
