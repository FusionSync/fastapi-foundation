from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.config.settings import DeploymentMode
from core.observability.monitoring import MonitoringContract, monitoring_contract
from core.security_hardening import SecurityHardeningItem, security_hardening_checklist


@dataclass(frozen=True, slots=True)
class ProcessTemplate:
    command: str
    replicas: int | str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "replicas": self.replicas,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class ProfileTemplate:
    profile: DeploymentMode
    env: dict[str, str]
    processes: dict[str, ProcessTemplate]
    validation_commands: list[str]
    security_hardening: tuple[SecurityHardeningItem, ...] = field(default_factory=tuple)
    monitoring: MonitoringContract | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": True,
            "profile": self.profile,
            "env": self.env,
            "processes": {
                role: template.to_dict() for role, template in self.processes.items()
            },
            "validation_commands": self.validation_commands,
            "security_hardening": [item.to_dict() for item in self.security_hardening],
            "notes": self.notes,
        }
        if self.monitoring is not None:
            payload["monitoring"] = self.monitoring.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class ConfigDriftReport:
    has_drift: bool
    checked: list[str]
    missing: list[dict[str, str]] = field(default_factory=list)
    mismatched: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "has_drift": self.has_drift,
            "checked": self.checked,
            "missing": self.missing,
            "mismatched": self.mismatched,
        }


def render_profile_template(profile: DeploymentMode) -> ProfileTemplate:
    if profile == "local":
        return _local_template()
    if profile == "private":
        return _private_template()
    return _cloud_template()


def check_profile_drift(
    profile: DeploymentMode,
    actual_env: dict[str, str],
    *,
    role: str | None = None,
) -> ConfigDriftReport:
    expected_env = expected_profile_env(profile, role=role)
    checked = list(expected_env)
    missing: list[dict[str, str]] = []
    mismatched: list[dict[str, str]] = []
    for key, expected in expected_env.items():
        actual = actual_env.get(key)
        if actual is None:
            missing.append({"key": key, "expected": _redact_value(key, expected)})
            continue
        if not _matches_expected(expected, actual):
            mismatched.append(
                {
                    "key": key,
                    "expected": _redact_value(key, expected),
                    "actual": _redact_value(key, actual),
                }
            )
    return ConfigDriftReport(
        has_drift=bool(missing or mismatched),
        checked=checked,
        missing=missing,
        mismatched=mismatched,
    )


def expected_profile_env(
    profile: DeploymentMode,
    *,
    role: str | None = None,
) -> dict[str, str]:
    template = render_profile_template(profile)
    env = dict(template.env)
    if role is not None:
        if role not in template.processes:
            raise ValueError(f"Unknown process role for {profile} profile: {role}")
        env["OBSERVABILITY__SERVICE_ROLE"] = role
    return env


def _local_template() -> ProfileTemplate:
    return ProfileTemplate(
        profile="local",
        env={
            "APP__ENV": "local",
            "DATABASE__URL": "sqlite+aiosqlite:///./data/local.db",
            "API__ERROR_HTTP_STATUS_MODE": "standard",
            "SECURITY__JWT_SECRET": "change-me",
            "SECURITY__TRUSTED_HOSTS": '["localhost","127.0.0.1","testserver"]',
            "OBSERVABILITY__SERVICE_ROLE": "server",
            "OUTBOX_DISPATCHER__BATCH_SIZE": "20",
            "OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS": "1.0",
            "INSTALLED_APPS": "[]",
        },
        processes={
            "server": ProcessTemplate(
                command="core serve --run --host 127.0.0.1 --port 8000",
                replicas=1,
            ),
            "worker": ProcessTemplate(
                command="core worker --run --instance-id ${INSTANCE_ID}",
                replicas=1,
            ),
            "scheduler": ProcessTemplate(
                command=(
                    "core scheduler --run --tenant-id ${TENANT_ID} "
                    "--instance-id ${INSTANCE_ID}"
                ),
                replicas=1,
            ),
            "outbox-dispatcher": ProcessTemplate(
                command=(
                    "core outbox-dispatcher --run --instance-id ${INSTANCE_ID} "
                    "--batch-size ${OUTBOX_DISPATCHER__BATCH_SIZE} "
                    "--idle-sleep-seconds ${OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS}"
                ),
                replicas=1,
            ),
            "migrate": ProcessTemplate(
                command="core migrate run --backup-ready --json",
                replicas=1,
            ),
        },
        validation_commands=[
            "core check-config --profile local --json",
            "core config drift-check --profile local --json",
            "core serve --run --dry-run --json",
            "core migrate run --backup-ready --json",
            "core smoke --profile local --json",
        ],
        security_hardening=security_hardening_checklist("local").items,
        monitoring=monitoring_contract("local"),
        notes=["Local profile is for development and single-node smoke checks."],
    )


def _private_template() -> ProfileTemplate:
    return ProfileTemplate(
        profile="private",
        env={
            "APP__ENV": "private",
            "DATABASE__URL": (
                "postgresql+asyncpg://app:${DATABASE_PASSWORD}@postgres:5432/wps_bid"
            ),
            "API__ERROR_HTTP_STATUS_MODE": "standard",
            "SECURITY__JWT_SECRET_REF": "APP_JWT_SECRET",
            "SECURITY__TRUSTED_HOSTS": '["api.internal.example"]',
            "SECURITY__CORS_ORIGINS": '["https://console.internal.example"]',
            "OBSERVABILITY__SERVICE_ROLE": "server",
            "OUTBOX_DISPATCHER__BATCH_SIZE": "20",
            "OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS": "1.0",
            "INSTALLED_APPS": "[]",
        },
        processes={
            "server": ProcessTemplate(
                command="core serve --run --host 0.0.0.0 --port 8000",
                replicas=2,
                notes=["Run behind private ingress or reverse proxy."],
            ),
            "worker": ProcessTemplate(
                command="core worker --run --instance-id ${INSTANCE_ID}",
                replicas=2,
            ),
            "scheduler": ProcessTemplate(
                command=(
                    "core scheduler --run --tenant-id ${TENANT_ID} "
                    "--instance-id ${INSTANCE_ID}"
                ),
                replicas=1,
                notes=["Use one active instance or external leader election."],
            ),
            "outbox-dispatcher": ProcessTemplate(
                command=(
                    "core outbox-dispatcher --run --instance-id ${INSTANCE_ID} "
                    "--batch-size ${OUTBOX_DISPATCHER__BATCH_SIZE} "
                    "--idle-sleep-seconds ${OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS}"
                ),
                replicas=2,
            ),
            "migrate": ProcessTemplate(
                command="core migrate run --backup-ready --json",
                replicas=1,
                notes=["Run as a one-shot release job before compatible code deploy."],
            ),
        },
        validation_commands=[
            "core check-config --profile private --json",
            "core config drift-check --profile private --json",
            "core serve --run --dry-run --json",
            "core migrate run --backup-ready --json",
            "core smoke --profile private --json",
        ],
        security_hardening=security_hardening_checklist("private").items,
        monitoring=monitoring_contract("private"),
        notes=["Private profile requires PostgreSQL and an external JWT secret."],
    )


def _cloud_template() -> ProfileTemplate:
    return ProfileTemplate(
        profile="cloud",
        env={
            "APP__ENV": "cloud",
            "DATABASE__URL": (
                "postgresql+asyncpg://app:${DATABASE_PASSWORD}@db.example.com:5432/wps_bid"
            ),
            "API__ERROR_HTTP_STATUS_MODE": "standard",
            "SECURITY__JWT_SECRET_REF": "APP_JWT_SECRET",
            "SECURITY__TRUSTED_HOSTS": '["api.example.com"]',
            "SECURITY__CORS_ORIGINS": '["https://console.example.com"]',
            "OBSERVABILITY__SERVICE_ROLE": "server",
            "OUTBOX_DISPATCHER__BATCH_SIZE": "20",
            "OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS": "1.0",
            "INSTALLED_APPS": "[]",
        },
        processes={
            "server": ProcessTemplate(
                command="core serve --run --host 0.0.0.0 --port 8000",
                replicas="autoscale",
            ),
            "worker": ProcessTemplate(
                command="core worker --run --instance-id ${INSTANCE_ID}",
                replicas="autoscale",
            ),
            "scheduler": ProcessTemplate(
                command=(
                    "core scheduler --run --tenant-id ${TENANT_ID} "
                    "--instance-id ${INSTANCE_ID}"
                ),
                replicas=1,
                notes=["Run as singleton or leader-elected job."],
            ),
            "outbox-dispatcher": ProcessTemplate(
                command=(
                    "core outbox-dispatcher --run --instance-id ${INSTANCE_ID} "
                    "--batch-size ${OUTBOX_DISPATCHER__BATCH_SIZE} "
                    "--idle-sleep-seconds ${OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS}"
                ),
                replicas="autoscale",
            ),
            "migrate": ProcessTemplate(
                command="core migrate run --backup-ready --json",
                replicas=1,
                notes=["Run as one-shot job with explicit approval in release pipeline."],
            ),
        },
        validation_commands=[
            "core check-config --profile cloud --json",
            "core config drift-check --profile cloud --json",
            "core serve --run --dry-run --json",
            "core migrate run --backup-ready --json",
            "core smoke --profile cloud --json",
        ],
        security_hardening=security_hardening_checklist("cloud").items,
        monitoring=monitoring_contract("cloud"),
        notes=["Cloud profile must keep standard HTTP statuses for probes and clients."],
    )


def _matches_expected(expected: str, actual: str) -> bool:
    if "${" not in expected:
        return actual == expected
    pattern = re.escape(expected)
    pattern = re.sub(r"\\\$\\\{[A-Z0-9_]+\\\}", r".+", pattern)
    return re.fullmatch(pattern, actual) is not None


def _redact_value(key: str, value: str) -> str:
    redacted = re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", value)
    normalized_key = key.upper()
    if _is_sensitive_key(normalized_key) and "${" not in value:
        return "***"
    return redacted


def _is_sensitive_key(key: str) -> bool:
    if key.endswith("_REF") or key.endswith("__JWT_SECRET_REF"):
        return False
    return (
        key.endswith("SECRET")
        or key.endswith("__JWT_SECRET")
        or "PASSWORD" in key
        or "TOKEN" in key
    )
