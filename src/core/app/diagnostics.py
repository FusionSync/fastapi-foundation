from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from fastapi import FastAPI

from core.operations import DependencyProbeResult


def build_startup_diagnostics(app: FastAPI) -> dict[str, object]:
    registries = _registry_diagnostics(app)
    providers = _provider_diagnostics(app)
    return {
        "ok": _diagnostics_ok(registries, providers),
        "registries": registries,
        "providers": providers,
    }


def merge_provider_readiness(
    startup_diagnostics: Mapping[str, Any] | None,
    dependency_results: Mapping[str, DependencyProbeResult],
) -> dict[str, object]:
    diagnostics = deepcopy(dict(startup_diagnostics or {}))
    registries = dict(diagnostics.get("registries") or {})
    providers = dict(diagnostics.get("providers") or {})
    for provider_name, result in dependency_results.items():
        providers[provider_name] = result.to_dict()
    diagnostics["registries"] = registries
    diagnostics["providers"] = providers
    diagnostics["ok"] = _diagnostics_ok(registries, providers)
    return diagnostics


def _registry_diagnostics(app: FastAPI) -> dict[str, dict[str, object]]:
    app_registry = getattr(app.state, "app_registry", None)
    admin_registry = getattr(app.state, "admin_registry", None)
    permission_registry = getattr(app.state, "permission_registry", None)
    migration_registry = getattr(app.state, "migration_registry", None)
    event_registry = getattr(app.state, "event_registry", None)
    task_registry = getattr(app.state, "task_registry", None)
    schedule_registry = getattr(app.state, "schedule_registry", None)
    metrics_registry = getattr(app.state, "metrics_registry", None)

    admin_payload = admin_registry.to_dict() if admin_registry is not None else {}
    event_payload = event_registry.to_dict() if event_registry is not None else {}
    task_payload = task_registry.to_dict() if task_registry is not None else {}
    schedule_payload = schedule_registry.to_dict() if schedule_registry is not None else {}

    return {
        "app": _registry_entry(
            ok=app_registry is not None
            and bool(getattr(app_registry, "diagnostics", None))
            and app_registry.diagnostics.ok,
            counts={
                "modules": len(getattr(app_registry, "modules", [])),
                "model_modules": len(getattr(app.state, "app_model_modules", [])),
                "routers": len(getattr(app_registry, "routers", [])) if app_registry else 0,
            },
            errors=getattr(getattr(app_registry, "diagnostics", None), "errors", []),
        ),
        "admin": _registry_entry(
            ok=admin_registry is not None,
            counts={
                "admin_permissions": len(admin_payload.get("admin_permissions", [])),
                "model_admins": len(admin_payload.get("model_admins", [])),
                "admin_routes": len(admin_payload.get("admin_routes", [])),
                "dashboard_widgets": len(admin_payload.get("dashboard_widgets", [])),
            },
        ),
        "permissions": _registry_entry(
            ok=permission_registry is not None and not permission_registry.errors,
            counts={
                "permissions": len(getattr(permission_registry, "permissions", [])),
            },
            errors=getattr(permission_registry, "errors", []),
        ),
        "migrations": _registry_entry(
            ok=migration_registry is not None and not migration_registry.errors,
            counts={
                "manifests": len(getattr(migration_registry, "manifests", [])),
            },
            errors=getattr(migration_registry, "errors", []),
        ),
        "events": _registry_entry(
            ok=event_registry is not None,
            counts={"handlers": len(event_payload.get("handlers", []))},
        ),
        "tasks": _registry_entry(
            ok=task_registry is not None,
            counts={"tasks": len(task_payload.get("tasks", []))},
        ),
        "schedules": _registry_entry(
            ok=schedule_registry is not None,
            counts={"schedules": len(schedule_payload.get("schedules", []))},
        ),
        "metrics": _registry_entry(
            ok=metrics_registry is not None,
            counts={},
        ),
    }


def _provider_diagnostics(app: FastAPI) -> dict[str, dict[str, object]]:
    settings = getattr(app.state, "settings", None)
    database_url = getattr(getattr(settings, "database", None), "url", "")
    return {
        "database": {
            "ok": bool(database_url),
            "details": {
                "service": "database",
                "configured": bool(database_url),
            },
        },
        "request_security": {
            "ok": True,
            "details": {
                "enabled": hasattr(app.state, "request_security_pipeline"),
            },
        },
    }


def _registry_entry(
    *,
    ok: bool,
    counts: Mapping[str, int],
    errors: list[str] | None = None,
) -> dict[str, object]:
    return {
        "ok": ok,
        "counts": dict(counts),
        "errors": list(errors or []),
    }


def _diagnostics_ok(
    registries: Mapping[str, Any],
    providers: Mapping[str, Any],
) -> bool:
    return all(_entry_ok(entry) for entry in registries.values()) and all(
        _entry_ok(entry) for entry in providers.values()
    )


def _entry_ok(entry: Any) -> bool:
    return isinstance(entry, Mapping) and entry.get("ok") is True
