from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MigrationPhase = Literal["expand", "backfill", "contract", "maintenance"]
MigrationClassification = Literal[
    "reversible",
    "forward_only",
    "destructive",
    "requires_backup_restore",
]
LockRisk = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class MigrationManifest:
    app_label: str
    migration_id: str
    phase: MigrationPhase
    classification: MigrationClassification
    depends_on: list[str] = field(default_factory=list)
    estimated_rows: int = 0
    lock_risk: LockRisk = "low"
    backfill_required: bool = False
    backfill_plan: str | None = None
    rollback_strategy: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    destructive_operations: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.app_label}:{self.migration_id}"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.app_label:
            errors.append("app_label is required")
        if not self.migration_id:
            errors.append("migration_id is required")
        if self.estimated_rows < 0:
            errors.append("estimated_rows cannot be negative")
        if self.backfill_required and not self.backfill_plan:
            errors.append(f"{self.key} requires backfill_plan")
        if self.lock_risk == "high" and self.estimated_rows > 0 and not self.backfill_plan:
            errors.append(f"{self.key} has high lock risk and must declare backfill_plan")
        if self.destructive_operations and self.classification in {"reversible", "forward_only"}:
            errors.append(
                f"{self.key} declares destructive operations but is not classified destructive"
            )
        if self.classification in {"destructive", "requires_backup_restore"}:
            if not self.approved_by or not self.approved_at:
                errors.append(f"{self.key} requires approved_by and approved_at")
            if not self.rollback_strategy:
                errors.append(f"{self.key} requires rollback_strategy")
        return errors

    def to_dict(self) -> dict[str, object]:
        return {
            "app_label": self.app_label,
            "migration_id": self.migration_id,
            "key": self.key,
            "phase": self.phase,
            "classification": self.classification,
            "depends_on": self.depends_on,
            "estimated_rows": self.estimated_rows,
            "lock_risk": self.lock_risk,
            "backfill_required": self.backfill_required,
            "backfill_plan": self.backfill_plan,
            "rollback_strategy": self.rollback_strategy,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "destructive_operations": self.destructive_operations,
        }
