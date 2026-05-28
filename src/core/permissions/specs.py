from dataclasses import dataclass
from typing import Literal

PermissionScope = Literal["tenant", "own", "resource", "platform"]
RiskLevel = Literal["low", "normal", "high", "critical"]


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    resource: str
    action: str
    scope: PermissionScope = "tenant"
    description: str = ""
    risk_level: RiskLevel = "normal"
