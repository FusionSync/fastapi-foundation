from __future__ import annotations

from dataclasses import dataclass, field

from core.config import Settings, validate_startup_settings
from core.config.settings import DeploymentMode
from core.db import verify_database_tenant_guard


@dataclass(frozen=True, slots=True)
class ConfigCheckResult:
    ok: bool
    profile: DeploymentMode
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "profile": self.profile,
            "errors": self.errors,
            "warnings": self.warnings,
            "details": self.details,
        }


def check_config(profile: DeploymentMode, settings: Settings | None = None) -> ConfigCheckResult:
    resolved_settings = settings or Settings(app={"env": profile})
    errors: list[str] = []
    warnings: list[str] = []
    errors.extend(_startup_setting_errors(resolved_settings))
    uses_sqlite = resolved_settings.database.url.startswith("sqlite")
    if profile in {"private", "cloud"} and uses_sqlite:
        errors.append(f"{profile} profile requires PostgreSQL database URL")
    database_guard = verify_database_tenant_guard(resolved_settings, profile=profile)
    if profile == "cloud":
        errors.extend(
            error
            for error in database_guard.errors
            if not (
                uses_sqlite
                and error == "cloud profile requires PostgreSQL database URL for tenant guard"
            )
        )
    if profile == "cloud" and not resolved_settings.security.cors_origins:
        warnings.append("cloud profile should declare explicit CORS origins")
    return ConfigCheckResult(
        ok=not errors,
        profile=profile,
        errors=errors,
        warnings=warnings,
        details={"database_tenant_guard": database_guard.to_dict()},
    )


def _startup_setting_errors(settings: Settings) -> list[str]:
    try:
        validate_startup_settings(settings)
    except ValueError as exc:
        message = str(exc)
        if (
            message == "Production-like profiles require SECURITY__JWT_SECRET to be changed"
            and settings.security.jwt_secret_ref
        ):
            return []
        return [message]
    return []
