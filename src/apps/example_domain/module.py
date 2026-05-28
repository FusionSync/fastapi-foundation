from apps.example_domain.permissions import PERMISSIONS
from apps.example_domain.router import router
from core.apps import AppModule, MigrationSpec

module = AppModule(
    label="example_domain",
    version="0.1.0",
    dependencies=[],
    routers=[router],
    models=["apps.example_domain.models"],
    migrations=MigrationSpec(path="apps.example_domain.migrations"),
    permissions=PERMISSIONS,
    public_api=["apps.example_domain.public_api"],
)
