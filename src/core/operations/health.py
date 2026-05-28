from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from core.config import Settings
from core.operations.heartbeat import ProcessHeartbeatSnapshot

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


def check_process_health(
    role: ProcessRole,
    settings: Settings | None = None,
    *,
    heartbeat: ProcessHeartbeatSnapshot | None = None,
    now: datetime | None = None,
    heartbeat_max_age_seconds: int = 120,
) -> ProcessHealth:
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
    if heartbeat is not None:
        heartbeat_now = _ensure_aware(now or datetime.now(UTC))
        heartbeat_seen_at = _ensure_aware(heartbeat.last_seen_at)
        heartbeat_age_seconds = max(0, int((heartbeat_now - heartbeat_seen_at).total_seconds()))

        checks["heartbeat_role_matches"] = heartbeat.role == role
        checks["heartbeat_status_healthy"] = heartbeat.status == "healthy"
        checks["heartbeat_fresh"] = heartbeat_age_seconds <= heartbeat_max_age_seconds
        details["heartbeat_instance_id"] = heartbeat.instance_id
        details["heartbeat_status"] = heartbeat.status
        details["heartbeat_last_seen_at"] = heartbeat_seen_at.isoformat()
        details["heartbeat_age_seconds"] = heartbeat_age_seconds
        details["heartbeat_max_age_seconds"] = heartbeat_max_age_seconds
        details["heartbeat_details"] = heartbeat.details
    return ProcessHealth(ok=all(checks.values()), role=role, checks=checks, details=details)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
