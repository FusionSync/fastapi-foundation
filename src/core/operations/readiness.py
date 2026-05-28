from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

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


@dataclass(frozen=True, slots=True)
class DependencyProbeResult:
    ok: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "details": self.details,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


class DatabaseReadinessProbe:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def check(self) -> DependencyProbeResult:
        if not self.database_url:
            return DependencyProbeResult(
                ok=False,
                details={"service": "database"},
                error="database URL is not configured",
            )
        engine = create_async_engine(self.database_url)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("select 1"))
        except Exception as exc:
            return DependencyProbeResult(
                ok=False,
                details={"service": "database"},
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            await engine.dispose()
        return DependencyProbeResult(ok=True, details={"service": "database"})


def check_app_readiness(
    *,
    settings: Settings,
    app_registry: AppRegistry | None,
    metrics_registry: MetricsRegistry | None,
    dependency_results: Mapping[str, DependencyProbeResult] | None = None,
    lifecycle_diagnostics: Mapping[str, list[dict[str, str]]] | None = None,
) -> ReadinessResult:
    startup_hooks_completed = _startup_hooks_completed(
        app_registry=app_registry,
        lifecycle_diagnostics=lifecycle_diagnostics,
    )
    checks = {
        "config_loaded": settings is not None,
        "database_configured": bool(settings.database.url),
        "app_registry_loaded": app_registry is not None,
        "metrics_registry_loaded": metrics_registry is not None,
        "lifecycle_startup_hooks_completed": startup_hooks_completed,
    }
    details = {
        "app_env": settings.app.env,
        "api_prefix": settings.api.prefix,
        "installed_apps": [
            module.label for module in app_registry.modules
        ]
        if app_registry is not None
        else [],
        "app_registry": app_registry.diagnostics.to_dict()
        if app_registry is not None
        else None,
        "dependencies": {},
        "lifecycle_hooks": _lifecycle_diagnostics_payload(lifecycle_diagnostics),
    }
    for dependency_name, result in (dependency_results or {}).items():
        checks[f"{dependency_name}_reachable"] = result.ok
        details["dependencies"][dependency_name] = result.to_dict()
    return ReadinessResult(ok=all(checks.values()), checks=checks, details=details)


def _startup_hooks_completed(
    *,
    app_registry: AppRegistry | None,
    lifecycle_diagnostics: Mapping[str, list[dict[str, str]]] | None,
) -> bool:
    if app_registry is None:
        return False
    expected = [
        (module.label, hook.hook_id)
        for module in app_registry.modules
        for hook in module.lifecycle_hooks
        if hook.phase == "startup"
    ]
    if not expected:
        return True
    succeeded = {
        (record.get("app_label", ""), record.get("hook_id", ""))
        for record in (lifecycle_diagnostics or {}).get("startup", [])
        if record.get("status") == "succeeded"
    }
    return all(hook_key in succeeded for hook_key in expected)


def _lifecycle_diagnostics_payload(
    lifecycle_diagnostics: Mapping[str, list[dict[str, str]]] | None,
) -> dict[str, list[dict[str, str]]]:
    return {
        "startup": list((lifecycle_diagnostics or {}).get("startup", [])),
        "shutdown": list((lifecycle_diagnostics or {}).get("shutdown", [])),
    }
