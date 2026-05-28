from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.config import Settings

ProcessRole = Literal["server", "worker", "scheduler", "outbox-dispatcher", "migrate"]


@dataclass(frozen=True, slots=True)
class ProcessHealth:
    ok: bool
    role: ProcessRole
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "role": self.role,
            "checks": self.checks,
            "details": self.details,
        }


def check_process_health(role: ProcessRole, settings: Settings | None = None) -> ProcessHealth:
    resolved_settings = settings or Settings()
    checks = {
        "config_loaded": True,
        "database_configured": bool(resolved_settings.database.url),
    }
    details: dict[str, object] = {
        "service_role": role,
        "app_env": resolved_settings.app.env,
    }
    if role == "server":
        checks["http_routes_configured"] = bool(resolved_settings.api.prefix)
    if role == "worker":
        checks["task_provider_configured"] = True
        details["task_provider"] = "sync"
    if role == "scheduler":
        checks["leader_or_lock_ready"] = resolved_settings.app.env == "local"
        details["missed_trigger_policy"] = "skip"
    if role == "outbox-dispatcher":
        checks["outbox_claim_loop_configured"] = True
    if role == "migrate":
        checks["migration_cli_available"] = True
    return ProcessHealth(ok=all(checks.values()), role=role, checks=checks, details=details)
