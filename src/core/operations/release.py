from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from core.apps.registry import AppRegistry
from core.config import Settings, check_profile_drift, render_deployment_artifacts
from core.config.profiles import expected_profile_env, render_profile_template
from core.config.settings import DeploymentMode
from core.migrations import (
    MigrationRegistry,
    dry_run_migration_metadata,
    plan_migrations,
    run_preflight,
)
from core.operations.backup import check_backup_readiness
from core.operations.config import check_config
from core.operations.health import ProcessRole
from core.operations.smoke import SMOKE_ROLES, run_deployment_smoke


@dataclass(frozen=True, slots=True)
class ReleaseCheckpointStage:
    name: str
    ok: bool
    result: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "result": self.result,
        }


@dataclass(frozen=True, slots=True)
class ReleaseCheckpointResult:
    ok: bool
    profile: DeploymentMode
    artifact_target: str
    profile_matrix: dict[str, dict[str, object]]
    stages: list[ReleaseCheckpointStage]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "profile": self.profile,
            "artifact_target": self.artifact_target,
            "profile_matrix": self.profile_matrix,
            "stages": [stage.to_dict() for stage in self.stages],
        }


def run_release_checkpoint(
    *,
    profile: DeploymentMode,
    artifact_target: str,
    actual_env: dict[str, str] | None = None,
    role_actual_env: dict[str, dict[str, str]] | None = None,
    latest_backup_at: datetime | None = None,
    max_backup_age_hours: int | None = None,
    installed_apps: list[str] | None = None,
) -> ReleaseCheckpointResult:
    template = render_profile_template(profile)
    settings = _settings_from_profile_env(expected_profile_env(profile))
    artifacts = render_deployment_artifacts(profile, artifact_target)  # type: ignore[arg-type]
    profile_matrix = {
        role: _matrix_entry(profile=profile, role=role) for role in template.processes
    }

    config_result = check_config(profile, settings=settings)
    backup_result = check_backup_readiness(
        profile=profile,
        latest_backup_at=latest_backup_at,
        max_age_hours=max_backup_age_hours,
    )
    drift_result = _run_role_drift_matrix(
        profile=profile,
        actual_env=actual_env or {},
        role_actual_env=role_actual_env or {},
    )
    migrate_result = _run_migrate_checkpoint(
        installed_apps=installed_apps or [],
        backup_ready=backup_result.ok,
    )
    smoke_result = run_deployment_smoke(profile, settings=settings)

    stages = [
        ReleaseCheckpointStage(
            name="profile-template",
            ok=True,
            result=template.to_dict(),
        ),
        ReleaseCheckpointStage(
            name="deployment-artifacts",
            ok=True,
            result=artifacts.to_dict(),
        ),
        ReleaseCheckpointStage(
            name="config-check",
            ok=config_result.ok,
            result=config_result.to_dict(),
        ),
        ReleaseCheckpointStage(
            name="backup-readiness",
            ok=backup_result.ok,
            result=backup_result.to_dict(),
        ),
        ReleaseCheckpointStage(
            name="config-drift",
            ok=bool(drift_result["ok"]),
            result=drift_result,
        ),
        ReleaseCheckpointStage(
            name="migrate-run",
            ok=bool(migrate_result["ok"]),
            result=migrate_result,
        ),
        ReleaseCheckpointStage(
            name="smoke",
            ok=smoke_result.ok,
            result=smoke_result.to_dict(),
        ),
    ]
    return ReleaseCheckpointResult(
        ok=all(stage.ok for stage in stages),
        profile=profile,
        artifact_target=artifact_target,
        profile_matrix=profile_matrix,
        stages=stages,
    )


def _matrix_entry(*, profile: DeploymentMode, role: str) -> dict[str, object]:
    template = render_profile_template(profile)
    process = template.processes[role]
    return {
        "command": process.command,
        "replicas": process.replicas,
        "env": expected_profile_env(profile, role=role),
        "drift_check_command": (
            f"core config drift-check --profile {profile} --role {role} --json"
        ),
        "notes": process.notes,
    }


def _run_role_drift_matrix(
    *,
    profile: DeploymentMode,
    actual_env: dict[str, str],
    role_actual_env: dict[str, dict[str, str]],
) -> dict[str, object]:
    use_actual = bool(actual_env or role_actual_env)
    roles: dict[ProcessRole, dict[str, object]] = {}
    for role in SMOKE_ROLES:
        env = (
            {**actual_env, **role_actual_env.get(role, {})}
            if use_actual
            else expected_profile_env(profile, role=role)
        )
        report = check_profile_drift(profile, env, role=role)
        roles[role] = {
            "ok": not report.has_drift,
            "drift": report.to_dict(),
        }
    return {
        "ok": all(role_result["ok"] for role_result in roles.values()),
        "mode": "actual-env" if use_actual else "profile-template",
        "roles": roles,
    }


def _run_migrate_checkpoint(
    *,
    installed_apps: list[str],
    backup_ready: bool,
) -> dict[str, object]:
    try:
        app_registry = AppRegistry(installed_apps).load()
        migration_registry = MigrationRegistry.from_app_registry(app_registry)
    except Exception as exc:
        return {
            "ok": False,
            "command": "migrate",
            "role": "migrate",
            "mode": "dry-run",
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
    plan = plan_migrations(migration_registry.manifests, app_registry=app_registry)
    plan_payload = {
        **plan.to_dict(),
        "ok": not migration_registry.errors and plan.ok,
        "registry_errors": migration_registry.errors,
    }
    preflight = run_preflight(
        migration_registry.manifests,
        backup_ready=backup_ready,
    )
    preflight_payload = {
        **preflight.to_dict(),
        "ok": not migration_registry.errors and preflight.ok,
        "registry_errors": migration_registry.errors,
    }
    dry_run = dry_run_migration_metadata(preflight)
    dry_run_payload = {
        **dry_run.to_dict(),
        "ok": not migration_registry.errors and dry_run.ok,
        "registry_errors": migration_registry.errors,
    }
    stages = [
        {"name": "plan", "ok": bool(plan_payload["ok"]), "result": plan_payload},
        {
            "name": "preflight",
            "ok": bool(preflight_payload["ok"]),
            "result": preflight_payload,
        },
        {"name": "dry-run", "ok": bool(dry_run_payload["ok"]), "result": dry_run_payload},
    ]
    return {
        "ok": all(stage["ok"] for stage in stages),
        "command": "migrate",
        "role": "migrate",
        "mode": "dry-run",
        "stages": stages,
    }


def _settings_from_profile_env(env: dict[str, str]) -> Settings:
    security: dict[str, object] = {}
    if "SECURITY__JWT_SECRET" in env:
        security["jwt_secret"] = env["SECURITY__JWT_SECRET"]
    if "SECURITY__JWT_SECRET_REF" in env:
        security["jwt_secret_ref"] = env["SECURITY__JWT_SECRET_REF"]
    if "SECURITY__TRUSTED_HOSTS" in env:
        security["trusted_hosts"] = json.loads(env["SECURITY__TRUSTED_HOSTS"])
    if "SECURITY__CORS_ORIGINS" in env:
        security["cors_origins"] = json.loads(env["SECURITY__CORS_ORIGINS"])
    return Settings(
        app={"env": env["APP__ENV"]},
        api={"error_http_status_mode": env["API__ERROR_HTTP_STATUS_MODE"]},
        database={"url": env["DATABASE__URL"]},
        security=security,
        observability={"service_role": env["OBSERVABILITY__SERVICE_ROLE"]},
        installed_apps=json.loads(env["INSTALLED_APPS"]),
    )
