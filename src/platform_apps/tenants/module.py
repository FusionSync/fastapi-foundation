from core.apps import AppModule, EventSchemaSpec, MigrationSpec
from platform_apps.tenants.permissions import PERMISSIONS
from platform_apps.tenants.router import router
from platform_apps.tenants.services import (
    TENANT_INVITATION_ACCEPTED_EVENT,
    TENANT_INVITATION_ISSUED_EVENT,
    TENANT_INVITATION_REVOKED_EVENT,
)

module = AppModule(
    label="platform_tenants",
    version="0.1.0",
    routers=[router],
    models=["platform_apps.tenants.models"],
    migrations=MigrationSpec(path="platform_apps.tenants.migrations"),
    permissions=PERMISSIONS,
    event_schemas=[
        EventSchemaSpec(
            event_type=TENANT_INVITATION_ISSUED_EVENT,
            event_version=1,
            required_payload_fields=["invitation_id", "email"],
            field_types={
                "invitation_id": "str",
                "email": "str",
            },
        ),
        EventSchemaSpec(
            event_type=TENANT_INVITATION_ACCEPTED_EVENT,
            event_version=1,
            required_payload_fields=["invitation_id", "user_id"],
            field_types={
                "invitation_id": "str",
                "user_id": "str",
            },
        ),
        EventSchemaSpec(
            event_type=TENANT_INVITATION_REVOKED_EVENT,
            event_version=1,
            required_payload_fields=["invitation_id"],
            field_types={"invitation_id": "str"},
        ),
    ],
    public_api=["platform_apps.tenants.public_api"],
)
