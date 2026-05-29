from core.apps import AppModule, EventSchemaSpec, MigrationSpec
from platform_apps.accounts.permissions import PERMISSIONS
from platform_apps.accounts.router import (
    auth_public_router,
    auth_router,
    me_router,
    platform_session_router,
    platform_user_router,
)
from platform_apps.accounts.services import (
    ACCOUNT_LOGIN_FAILED_EVENT,
    ACCOUNT_SESSION_CREATED_EVENT,
    ACCOUNT_SESSION_REFRESHED_EVENT,
    ACCOUNT_SESSION_REVOKED_EVENT,
    ACCOUNT_USER_DISABLED_EVENT,
)

module = AppModule(
    label="platform_accounts",
    version="0.1.0",
    routers=[
        auth_public_router,
        auth_router,
        me_router,
        platform_user_router,
        platform_session_router,
    ],
    models=["platform_apps.accounts.models"],
    migrations=MigrationSpec(path="platform_apps.accounts.migrations"),
    permissions=PERMISSIONS,
    event_schemas=[
        EventSchemaSpec(
            event_type=ACCOUNT_SESSION_CREATED_EVENT,
            event_version=1,
            required_payload_fields=["session_id", "user_id", "auth_provider"],
            field_types={
                "session_id": "str",
                "user_id": "str",
                "auth_provider": "str",
                "token_version": "int",
            },
        ),
        EventSchemaSpec(
            event_type=ACCOUNT_SESSION_REFRESHED_EVENT,
            event_version=1,
            required_payload_fields=["session_id", "user_id", "auth_provider"],
            field_types={
                "session_id": "str",
                "user_id": "str",
                "auth_provider": "str",
                "token_version": "int",
            },
        ),
        EventSchemaSpec(
            event_type=ACCOUNT_LOGIN_FAILED_EVENT,
            event_version=1,
            required_payload_fields=["email", "auth_provider", "reason"],
            field_types={
                "email": "str",
                "auth_provider": "str",
                "reason": "str",
            },
        ),
        EventSchemaSpec(
            event_type=ACCOUNT_SESSION_REVOKED_EVENT,
            event_version=1,
            required_payload_fields=["scope", "reason", "revoked_sessions"],
            field_types={
                "scope": "str",
                "reason": "str",
                "revoked_sessions": "int",
            },
        ),
        EventSchemaSpec(
            event_type=ACCOUNT_USER_DISABLED_EVENT,
            event_version=1,
            required_payload_fields=["user_id", "reason", "revoked_sessions", "token_version"],
            field_types={
                "user_id": "str",
                "reason": "str",
                "revoked_sessions": "int",
                "token_version": "int",
            },
        ),
    ],
    auth_session_store="platform_apps.accounts.public_api.AccountsAuthSessionStore",
    public_api=["platform_apps.accounts.public_api"],
)
