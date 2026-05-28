from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.apps import AppRegistry
from core.config import Settings
from core.observability import MetricsRegistry


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    ok: bool
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ok else "not_ready",
            "checks": self.checks,
            "details": self.details,
        }


def check_app_readiness(
    *,
    settings: Settings,
    app_registry: AppRegistry | None,
    metrics_registry: MetricsRegistry | None,
) -> ReadinessResult:
    checks = {
        "config_loaded": settings is not None,
        "database_configured": bool(settings.database.url),
        "app_registry_loaded": app_registry is not None,
        "metrics_registry_loaded": metrics_registry is not None,
    }
    details = {
        "app_env": settings.app.env,
        "api_prefix": settings.api.prefix,
        "installed_apps": [
            module.label for module in app_registry.modules
        ]
        if app_registry is not None
        else [],
    }
    return ReadinessResult(ok=all(checks.values()), checks=checks, details=details)
