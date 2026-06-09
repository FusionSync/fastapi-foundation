from apps.example_domain.error_messages import MESSAGE_CATALOGS
from apps.example_domain.errors import ERROR_CODES
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
    error_codes=ERROR_CODES,
    message_catalogs=MESSAGE_CATALOGS,
    public_api=["apps.example_domain.public_api"],
)
