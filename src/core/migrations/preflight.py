from __future__ import annotations

from dataclasses import dataclass, field

from core.migrations.drift import DriftReport
from core.migrations.manifest import MigrationManifest, MigrationPhase
from core.migrations.planner import MigrationPlan, plan_migrations


@dataclass(slots=True)
class PreflightResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    plan: MigrationPlan | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "plan": self.plan.to_dict() if self.plan else None,
        }


def run_preflight(
    manifests: list[MigrationManifest],
    *,
    drift_report: DriftReport | None = None,
    backup_ready: bool = False,
    phase: MigrationPhase | None = None,
) -> PreflightResult:
    errors: list[str] = []
    warnings: list[str] = []
    plan = plan_migrations(manifests, phase=phase)
    errors.extend(plan.errors)

    if drift_report and drift_report.has_drift:
        errors.extend(f"schema drift: {detail}" for detail in drift_report.details)

    for manifest in plan.migrations:
        errors.extend(manifest.validate())
        backup_required = manifest.classification in {
            "destructive",
            "requires_backup_restore",
        }
        if backup_required and not backup_ready:
            errors.append(f"{manifest.key} requires backup readiness before execution")
        if manifest.classification == "forward_only":
            warnings.append(f"{manifest.key} is forward-only; rollback must use forward fix")

    return PreflightResult(ok=not errors, errors=errors, warnings=warnings, plan=plan)
