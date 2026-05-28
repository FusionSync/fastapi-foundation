from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from core.config.settings import DeploymentMode


@dataclass(frozen=True, slots=True)
class BackupReadinessResult:
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


def check_backup_readiness(
    *,
    profile: DeploymentMode,
    latest_backup_at: datetime | None = None,
    now: datetime | None = None,
    max_age_hours: int | None = None,
) -> BackupReadinessResult:
    if profile == "local":
        return BackupReadinessResult(
            ok=True,
            profile=profile,
            warnings=["local profile uses best-effort backup readiness"],
        )

    resolved_now = now or datetime.now(UTC)
    if latest_backup_at is None:
        return BackupReadinessResult(
            ok=False,
            profile=profile,
            errors=["latest_backup_at is required for production-like profiles"],
        )
    backup_age = resolved_now - latest_backup_at
    allowed_age = timedelta(hours=max_age_hours or (1 if profile == "cloud" else 24))
    if backup_age > allowed_age:
        return BackupReadinessResult(
            ok=False,
            profile=profile,
            errors=[f"latest backup is older than {allowed_age}"],
        )
    return BackupReadinessResult(ok=True, profile=profile)


def parse_backup_time(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
