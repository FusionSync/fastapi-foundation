from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from core.config import Settings
from core.config.settings import DeploymentMode

DependencyProbeKind = Literal["database", "queue", "redis_tcp", "http"]
DependencyProbeStatus = Literal["configured", "missing", "passed", "failed"]
DependencyProbeRunner = Callable[[str, str], "DependencyProbeOutcome"]

_PRODUCTION_PROFILES = {"private", "cloud"}


@dataclass(frozen=True, slots=True)
class DependencyProbeOutcome:
    ok: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DependencyProbeSpec:
    name: str
    kind: DependencyProbeKind
    provider: str
    required: bool
    target: str | None


@dataclass(frozen=True, slots=True)
class DependencyProbeCheck:
    spec: DependencyProbeSpec
    ok: bool
    status: DependencyProbeStatus
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "status": self.status,
            "required": self.spec.required,
            "kind": self.spec.kind,
            "provider": self.spec.provider,
            "target": _redact_target(self.spec.target),
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True, slots=True)
class ProfileDependencyCheckResult:
    ok: bool
    profile: DeploymentMode
    mode: str
    probes: tuple[DependencyProbeCheck, ...]
    errors: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "profile": self.profile,
            "mode": self.mode,
            "probes": {probe.spec.name: probe.to_dict() for probe in self.probes},
            "errors": list(self.errors),
        }


def check_profile_dependencies(
    profile: DeploymentMode,
    settings: Settings,
    *,
    run_actual: bool = False,
    probe_runner: DependencyProbeRunner | None = None,
) -> ProfileDependencyCheckResult:
    runner = probe_runner or _default_probe_runner
    probes: list[DependencyProbeCheck] = []
    errors: list[str] = []
    for spec in _dependency_specs(profile, settings):
        check = _check_dependency(
            profile=profile,
            spec=spec,
            run_actual=run_actual,
            probe_runner=runner,
        )
        probes.append(check)
        if spec.required and not check.ok:
            errors.append(f"{spec.name}: {check.error or 'dependency probe failed'}")
    return ProfileDependencyCheckResult(
        ok=not errors,
        profile=profile,
        mode="actual-probes" if run_actual else "configuration",
        probes=tuple(probes),
        errors=errors,
    )


def _dependency_specs(
    profile: DeploymentMode,
    settings: Settings,
) -> tuple[DependencyProbeSpec, ...]:
    production = profile in _PRODUCTION_PROFILES
    return (
        DependencyProbeSpec(
            name="database",
            kind="database",
            provider=_database_provider(settings.database.url),
            required=True,
            target=settings.database.url,
        ),
        DependencyProbeSpec(
            name="task_queue",
            kind="queue",
            provider=settings.task_queue.provider,
            required=True,
            target=(
                "database.task_runs"
                if settings.task_queue.provider == "database"
                else settings.task_queue.provider
            ),
        ),
        DependencyProbeSpec(
            name="redis",
            kind="redis_tcp",
            provider="redis",
            required=production,
            target=settings.dependencies.redis_url,
        ),
        DependencyProbeSpec(
            name="object_storage",
            kind="http",
            provider="object-storage",
            required=production,
            target=settings.dependencies.object_storage_endpoint,
        ),
        DependencyProbeSpec(
            name="oidc",
            kind="http",
            provider="oidc",
            required=production,
            target=settings.dependencies.oidc_issuer_url,
        ),
    )


def _check_dependency(
    *,
    profile: DeploymentMode,
    spec: DependencyProbeSpec,
    run_actual: bool,
    probe_runner: DependencyProbeRunner,
) -> DependencyProbeCheck:
    if not spec.target:
        status: DependencyProbeStatus = "missing"
        ok = not spec.required
        return DependencyProbeCheck(
            spec=spec,
            ok=ok,
            status=status,
            error="required dependency target is not configured" if spec.required else None,
        )
    if spec.name == "task_queue" and profile in _PRODUCTION_PROFILES and spec.provider == "sync":
        return DependencyProbeCheck(
            spec=spec,
            ok=False,
            status="failed",
            error=f"{profile} profile requires non-sync task queue provider",
        )
    if not run_actual:
        return DependencyProbeCheck(spec=spec, ok=True, status="configured")
    outcome = probe_runner(spec.name, spec.target)
    return DependencyProbeCheck(
        spec=spec,
        ok=outcome.ok,
        status="passed" if outcome.ok else "failed",
        error=outcome.error,
    )


def _default_probe_runner(name: str, target: str) -> DependencyProbeOutcome:
    parsed = urlsplit(target)
    try:
        if parsed.scheme in {"redis", "rediss"}:
            return _tcp_probe(parsed.hostname, parsed.port or 6379)
        if parsed.scheme in {"http", "https"}:
            return _http_probe(target)
    except Exception as exc:
        return DependencyProbeOutcome(ok=False, error=f"{type(exc).__name__}: {exc}")
    return DependencyProbeOutcome(ok=True)


def _tcp_probe(host: str | None, port: int) -> DependencyProbeOutcome:
    if not host:
        return DependencyProbeOutcome(ok=False, error="missing host")
    with socket.create_connection((host, port), timeout=2):
        return DependencyProbeOutcome(ok=True)


def _http_probe(target: str) -> DependencyProbeOutcome:
    request = Request(target, method="HEAD")
    with urlopen(request, timeout=2) as response:
        status = getattr(response, "status", 200)
    if status >= 500:
        return DependencyProbeOutcome(ok=False, error=f"HTTP {status}")
    return DependencyProbeOutcome(ok=True)


def _database_provider(database_url: str) -> str:
    scheme = database_url.split(":", 1)[0].lower()
    if scheme.startswith("postgres"):
        return "postgresql"
    if scheme.startswith("sqlite"):
        return "sqlite"
    return scheme or "unknown"


def _redact_target(target: str | None) -> str | None:
    if target is None:
        return None
    try:
        parsed = urlsplit(target)
    except ValueError:
        return target
    if parsed.password is None:
        return target
    username = parsed.username or ""
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    netloc = f"{username}:***@{host}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
