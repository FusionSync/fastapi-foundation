from __future__ import annotations

from dataclasses import dataclass, field

from core.migrations.manifest import MigrationManifest
from core.migrations.preflight import PreflightResult


@dataclass(frozen=True, slots=True)
class MigrationApplyResult:
    ok: bool
    applied: bool
    mode: str = "metadata"
    migrations: list[MigrationManifest] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "applied": self.applied,
            "mode": self.mode,
            "migrations": [manifest.to_dict() for manifest in self.migrations],
            "errors": self.errors,
            "warnings": self.warnings,
        }


def apply_migration_metadata(
    preflight: PreflightResult,
) -> MigrationApplyResult:
    if not preflight.ok or preflight.plan is None:
        return MigrationApplyResult(
            ok=False,
            applied=False,
            migrations=preflight.plan.migrations if preflight.plan else [],
            errors=preflight.errors,
            warnings=preflight.warnings,
        )
    return MigrationApplyResult(
        ok=True,
        applied=True,
        migrations=preflight.plan.migrations,
        errors=[],
        warnings=preflight.warnings,
    )
