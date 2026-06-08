from core.apps import AppModule, EventSchemaSpec, MigrationSpec
from core.permissions.projector import ROLE_GRANT_CHANGED_EVENT
from platform_apps.access.permissions import PERMISSIONS
from platform_apps.access.router import permission_router, platform_admin_router

module = AppModule(
    label="platform_access",
    version="0.1.0",
    routers=[permission_router, platform_admin_router],
    models=["platform_apps.access.models"],
    migrations=MigrationSpec(path="platform_apps.access.migrations"),
    permissions=PERMISSIONS,
    event_schemas=[
        EventSchemaSpec(
            event_type=ROLE_GRANT_CHANGED_EVENT,
            event_version=1,
            required_payload_fields=["grant_id", "subject_type", "subject_id"],
            field_types={
                "grant_id": "str",
                "subject_type": "str",
                "subject_id": "str",
                "role_template_id": "str",
            },
        ),
    ],
    public_api=["platform_apps.access.public_api"],
)
