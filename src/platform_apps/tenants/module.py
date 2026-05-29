from core.apps import AppModule, EventSchemaSpec, MigrationSpec
from core.exceptions import ErrorCodeSpec
from core.tenancy import TENANT_MEMBER_ACTIVATED_EVENT
from platform_apps.tenants.permissions import PERMISSIONS
from platform_apps.tenants.router import (
    invitation_accept_router,
    invitation_issue_router,
    invitation_revoke_router,
    member_manage_router,
    member_read_router,
    platform_router,
)
from platform_apps.tenants.services import (
    TENANT_INVITATION_ACCEPTED_EVENT,
    TENANT_INVITATION_ISSUED_EVENT,
    TENANT_INVITATION_REVOKED_EVENT,
)

module = AppModule(
    label="platform_tenants",
    version="0.1.0",
    routers=[
        platform_router,
        member_read_router,
        member_manage_router,
        invitation_issue_router,
        invitation_revoke_router,
        invitation_accept_router,
    ],
    models=["platform_apps.tenants.models"],
    migrations=MigrationSpec(path="platform_apps.tenants.migrations"),
    permissions=PERMISSIONS,
    error_codes=[
        ErrorCodeSpec(
            "PLATFORM_TENANTS_HTTP_NOT_READY",
            501,
            "Platform tenants HTTP endpoint is not connected yet",
            owner_module="platform_tenants",
            details_schema={},
            deprecated=False,
        )
    ],
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
        EventSchemaSpec(
            event_type=TENANT_MEMBER_ACTIVATED_EVENT,
            event_version=1,
            required_payload_fields=["member_id", "user_id", "status"],
            field_types={
                "member_id": "str",
                "user_id": "str",
                "status": "str",
            },
        ),
    ],
    public_api=["platform_apps.tenants.public_api"],
)
