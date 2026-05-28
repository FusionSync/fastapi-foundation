from __future__ import annotations

from dataclasses import dataclass, field

from core.config import Settings, validate_startup_settings
from core.config.settings import DeploymentMode


@dataclass(frozen=True, slots=True)
class ConfigCheckResult:
    ok: bool
    profile: DeploymentMode
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "profile": self.profile,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def check_config(profile: DeploymentMode, settings: Settings | None = None) -> ConfigCheckResult:
    resolved_settings = settings or Settings(app={"env": profile})
    errors: list[str] = []
    warnings: list[str] = []
    try:
        validate_startup_settings(resolved_settings)
    except ValueError as exc:
        errors.append(str(exc))
    if profile in {"private", "cloud"} and resolved_settings.database.url.startswith("sqlite"):
        errors.append(f"{profile} profile requires PostgreSQL database URL")
    if profile == "cloud" and not resolved_settings.security.cors_origins:
        warnings.append("cloud profile should declare explicit CORS origins")
    return ConfigCheckResult(ok=not errors, profile=profile, errors=errors, warnings=warnings)
