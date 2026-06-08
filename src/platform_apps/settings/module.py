from core.apps import AppModule, EventSchemaSpec, MigrationSpec
from platform_apps.settings.definitions import BUILTIN_SETTINGS
from platform_apps.settings.permissions import PERMISSIONS
from platform_apps.settings.router import (
    definition_router,
    platform_resolve_router,
    platform_value_router,
    tenant_value_router,
)
from platform_apps.settings.services import PLATFORM_SETTING_VALUE_CHANGED_EVENT

module = AppModule(
    label="platform_settings",
    version="0.1.0",
    routers=[
        definition_router,
        platform_value_router,
        platform_resolve_router,
        tenant_value_router,
    ],
    models=["platform_apps.settings.models"],
    migrations=MigrationSpec(path="platform_apps.settings.migrations"),
    permissions=PERMISSIONS,
    event_schemas=[
        EventSchemaSpec(
            event_type=PLATFORM_SETTING_VALUE_CHANGED_EVENT,
            event_version=1,
            required_payload_fields=[
                "setting_value_id",
                "module",
                "key",
                "scope",
                "scope_id",
                "version",
            ],
            field_types={
                "setting_value_id": "str",
                "module": "str",
                "key": "str",
                "scope": "str",
                "scope_id": "str",
                "version": "int",
            },
        ),
    ],
    settings=BUILTIN_SETTINGS,
    public_api=["platform_apps.settings.public_api"],
)
