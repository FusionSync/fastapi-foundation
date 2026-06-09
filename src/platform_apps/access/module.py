from core.apps import AppModule, EventHandlerSpec, EventSchemaSpec, MigrationSpec
from core.permissions.projector import ROLE_GRANT_CHANGED_EVENT
from platform_apps.access.permissions import PERMISSIONS
from platform_apps.access.router import (
    effective_access_router,
    me_permission_router,
    permission_router,
    platform_admin_router,
    projection_reconcile_router,
    role_grant_grant_router,
    role_grant_read_router,
    role_grant_revoke_router,
    role_template_manage_router,
    role_template_read_router,
)

module = AppModule(
    label="platform_access",
    version="0.1.0",
    routers=[
        permission_router,
        platform_admin_router,
        role_template_read_router,
        role_template_manage_router,
        role_grant_read_router,
        role_grant_grant_router,
        role_grant_revoke_router,
        me_permission_router,
        effective_access_router,
        projection_reconcile_router,
    ],
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
    event_handlers=[
        EventHandlerSpec(
            event_type=ROLE_GRANT_CHANGED_EVENT,
            event_version=1,
            handler_path="platform_apps.access.handlers.handle_role_grant_changed",
        ),
    ],
    public_api=["platform_apps.access.public_api"],
)
