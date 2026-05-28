from __future__ import annotations

from dataclasses import dataclass, field

from core.config.settings import DeploymentMode
from core.operations.config import check_config
from core.operations.health import check_process_health


@dataclass(frozen=True, slots=True)
class SmokeResult:
    ok: bool
    profile: DeploymentMode
    checks: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "profile": self.profile,
            "checks": self.checks,
            "errors": self.errors,
        }


def run_deployment_smoke(profile: DeploymentMode) -> SmokeResult:
    config_result = check_config(profile)
    server_health = check_process_health("server")
    checks = {
        "config": config_result.ok,
        "server_health": server_health.ok,
    }
    errors = [*config_result.errors]
    if not server_health.ok:
        errors.append("server health check failed")
    return SmokeResult(ok=all(checks.values()), profile=profile, checks=checks, errors=errors)
