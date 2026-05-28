from __future__ import annotations

from dataclasses import dataclass, field

from core.config import Settings
from core.config.settings import DeploymentMode
from core.operations.config import check_config
from core.operations.health import ProcessHealth, ProcessRole, check_process_health

SMOKE_ROLES: tuple[ProcessRole, ...] = (
    "server",
    "worker",
    "scheduler",
    "outbox-dispatcher",
    "migrate",
)


@dataclass(frozen=True, slots=True)
class SmokeResult:
    ok: bool
    profile: DeploymentMode
    checks: dict[str, bool] = field(default_factory=dict)
    role_health: dict[ProcessRole, ProcessHealth] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "profile": self.profile,
            "checks": self.checks,
            "role_health": {
                role: health.to_dict() for role, health in self.role_health.items()
            },
            "errors": self.errors,
        }


def run_deployment_smoke(profile: DeploymentMode) -> SmokeResult:
    settings = Settings(app={"env": profile})
    config_result = check_config(profile, settings=settings)
    role_health = {
        role: check_process_health(role, settings=settings)
        for role in SMOKE_ROLES
    }
    checks = {"config": config_result.ok}
    checks.update(
        {
            f"{role.replace('-', '_')}_health": health.ok
            for role, health in role_health.items()
        }
    )
    errors = [*config_result.errors]
    for role, health in role_health.items():
        if not health.ok:
            failed_checks = [
                check_name for check_name, ok in health.checks.items() if not ok
            ]
            errors.append(f"{role} health check failed: {failed_checks}")
    return SmokeResult(
        ok=all(checks.values()),
        profile=profile,
        checks=checks,
        role_health=role_health,
        errors=errors,
    )
