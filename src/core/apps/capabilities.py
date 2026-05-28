from __future__ import annotations

from core.config.settings import Settings

BASE_RUNTIME_CAPABILITIES = frozenset(
    {
        "admin",
        "events",
        "lifecycle",
        "migrations",
        "outbox",
        "permissions",
        "scheduler",
        "tasks",
        "tenancy",
    }
)
DEFAULT_RUNTIME_CAPABILITIES = BASE_RUNTIME_CAPABILITIES | {
    "auth",
    "database",
    "observability.metrics",
    "profile.local",
    "provider.auth.local_jwt",
    "provider.database.sqlite",
    "role.server",
}


def resolve_runtime_capabilities(
    settings: Settings,
    *,
    database_url: str | None = None,
    service_role: str | None = None,
) -> set[str]:
    effective_database_url = database_url if database_url is not None else settings.database.url
    effective_service_role = service_role or settings.observability.service_role
    capabilities = set(BASE_RUNTIME_CAPABILITIES)
    capabilities.add(f"profile.{settings.app.env}")
    capabilities.add(f"role.{effective_service_role}")

    if effective_database_url:
        capabilities.add("database")
        database_provider = _database_provider_capability(effective_database_url)
        if database_provider is not None:
            capabilities.add(database_provider)

    local_jwt_enabled = _local_jwt_enabled(
        jwt_secret=settings.security.jwt_secret,
        jwt_secret_ref=settings.security.jwt_secret_ref,
    )
    if local_jwt_enabled or settings.security.jwt_secret_ref:
        capabilities.add("auth")
    if local_jwt_enabled:
        capabilities.add("provider.auth.local_jwt")
    if settings.security.jwt_secret_ref:
        capabilities.add("provider.auth.external_secret")

    if settings.observability.metrics_enabled:
        capabilities.add("observability.metrics")

    return capabilities


def _database_provider_capability(database_url: str) -> str | None:
    scheme = database_url.split(":", 1)[0].lower()
    if scheme.startswith("sqlite"):
        return "provider.database.sqlite"
    if scheme.startswith("postgresql") or scheme.startswith("postgres"):
        return "provider.database.postgresql"
    return None


def _local_jwt_enabled(*, jwt_secret: str, jwt_secret_ref: str | None) -> bool:
    if not jwt_secret:
        return False
    return jwt_secret != "change-me" or jwt_secret_ref is None
