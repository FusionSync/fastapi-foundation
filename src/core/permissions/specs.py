from dataclasses import dataclass
from typing import Literal

PermissionScope = Literal["tenant", "own", "resource", "platform"]
RiskLevel = Literal["low", "normal", "high", "critical"]
VALID_PERMISSION_SCOPES = frozenset(("tenant", "own", "resource", "platform"))
VALID_RISK_LEVELS = frozenset(("low", "normal", "high", "critical"))


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    resource: str
    action: str
    scope: PermissionScope = "tenant"
    description: str = ""
    risk_level: RiskLevel = "normal"

    def __post_init__(self) -> None:
        _require_clean_text(self.resource, "resource")
        _require_clean_text(self.action, "action")
        if self.scope not in VALID_PERMISSION_SCOPES:
            raise ValueError(f"invalid permission scope: {self.scope!r}")
        if self.risk_level not in VALID_RISK_LEVELS:
            raise ValueError(f"invalid permission risk_level: {self.risk_level!r}")


def _require_clean_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"permission {field_name} is required")
    if value != value.strip():
        raise ValueError(f"permission {field_name} must not include surrounding whitespace")
