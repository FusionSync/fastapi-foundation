from __future__ import annotations

from dataclasses import dataclass, field

from core.config.settings import DeploymentMode


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
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "profile": self.profile,
            "env": self.env,
            "processes": {
                role: template.to_dict() for role, template in self.processes.items()
            },
            "validation_commands": self.validation_commands,
            "notes": self.notes,
        }


def render_profile_template(profile: DeploymentMode) -> ProfileTemplate:
    if profile == "local":
        return _local_template()
    if profile == "private":
        return _private_template()
    return _cloud_template()


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
                command="core outbox-dispatcher --run --instance-id ${INSTANCE_ID}",
                replicas=1,
            ),
            "migrate": ProcessTemplate(
                command="core migrate run --backup-ready --json",
                replicas=1,
            ),
        },
        validation_commands=[
            "core check-config --profile local --json",
            "core serve --run --dry-run --json",
            "core migrate run --backup-ready --json",
            "core smoke --profile local --json",
        ],
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
                command="core outbox-dispatcher --run --instance-id ${INSTANCE_ID}",
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
            "core serve --run --dry-run --json",
            "core migrate run --backup-ready --json",
            "core smoke --profile private --json",
        ],
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
                command="core outbox-dispatcher --run --instance-id ${INSTANCE_ID}",
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
            "core serve --run --dry-run --json",
            "core migrate run --backup-ready --json",
            "core smoke --profile cloud --json",
        ],
        notes=["Cloud profile must keep standard HTTP statuses for probes and clients."],
    )
