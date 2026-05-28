from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DriftReport:
    has_drift: bool
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"has_drift": self.has_drift, "details": self.details}


def check_drift(
    expected_heads: Mapping[str, str],
    actual_heads: Mapping[str, str],
) -> DriftReport:
    details: list[str] = []
    for app_label, expected_head in expected_heads.items():
        actual_head = actual_heads.get(app_label)
        if actual_head != expected_head:
            details.append(
                f"{app_label}: expected {expected_head!r}, actual {actual_head!r}"
            )
    for app_label in actual_heads:
        if app_label not in expected_heads:
            details.append(f"{app_label}: unexpected database head {actual_heads[app_label]!r}")
    return DriftReport(has_drift=bool(details), details=details)
