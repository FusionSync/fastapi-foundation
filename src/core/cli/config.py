from __future__ import annotations

import argparse
import os
from pathlib import Path

from core.cli.common import CLI_USAGE_ERROR, error_payload, print_payload
from core.config import (
    ConfigDriftReport,
    DeploymentArtifact,
    check_profile_drift,
    render_deployment_artifacts,
    render_deployment_bundle_artifacts,
    render_profile_template,
)
from core.config.settings import DeploymentMode
from core.observability import config_drift_alerts

_PROFILES = ["local", "private", "cloud"]
_ARTIFACT_TARGETS = ["docker-compose", "systemd", "helm-values"]
_PROCESS_ROLES = ["server", "worker", "scheduler", "outbox-dispatcher", "migrate"]


def register_config_commands(subparsers: argparse._SubParsersAction) -> None:
    config_parser = subparsers.add_parser("config")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    template_parser = config_subparsers.add_parser("template")
    template_parser.add_argument("--profile", choices=_PROFILES, required=True)
    template_parser.add_argument("--json", action="store_true", dest="as_json")
    template_parser.set_defaults(handler=_handle_config_template)

    drift_parser = config_subparsers.add_parser("drift-check")
    drift_parser.add_argument("--profile", choices=_PROFILES, required=True)
    drift_parser.add_argument("--role", choices=_PROCESS_ROLES)
    drift_parser.add_argument("--actual", action="append", default=[])
    drift_parser.add_argument("--json", action="store_true", dest="as_json")
    drift_parser.set_defaults(handler=_handle_config_drift_check)

    artifacts_parser = config_subparsers.add_parser("artifacts")
    artifacts_parser.add_argument("--profile", choices=_PROFILES, required=True)
    artifacts_parser.add_argument("--target", choices=_ARTIFACT_TARGETS, required=True)
    artifacts_parser.add_argument("--role", choices=_PROCESS_ROLES)
    artifacts_parser.add_argument("--actual", action="append", default=[])
    artifacts_parser.add_argument("--json", action="store_true", dest="as_json")
    artifacts_parser.set_defaults(handler=_handle_config_artifacts)

    write_artifacts_parser = config_subparsers.add_parser("write-artifacts")
    write_artifacts_parser.add_argument("--profile", choices=_PROFILES, required=True)
    write_artifacts_parser.add_argument("--target", choices=_ARTIFACT_TARGETS, required=True)
    write_artifacts_parser.add_argument("--output-dir", required=True)
    write_artifacts_parser.add_argument("--json", action="store_true", dest="as_json")
    write_artifacts_parser.set_defaults(handler=_handle_config_write_artifacts)


def _handle_config_template(args: argparse.Namespace) -> int:
    template = render_profile_template(args.profile)
    payload = {
        **template.to_dict(),
        "command": "config template",
    }
    print_payload(payload, as_json=args.as_json)
    return 0


def _handle_config_drift_check(args: argparse.Namespace) -> int:
    try:
        actual_env = _parse_actual_env(args.actual)
    except ValueError as exc:
        print_payload(
            error_payload(
                code=CLI_USAGE_ERROR,
                message=str(exc),
                command="config drift-check",
                exit_code=2,
            ),
            as_json=args.as_json,
        )
        return 2
    report = check_profile_drift(args.profile, actual_env, role=args.role)
    payload = {
        "ok": not report.has_drift,
        "command": "config drift-check",
        "profile": args.profile,
        "drift": report.to_dict(),
        "alerts": _drift_alerts(args.profile, report, role=args.role),
    }
    if args.role is not None:
        payload["role"] = args.role
    print_payload(payload, as_json=args.as_json)
    return 1 if report.has_drift else 0


def _handle_config_artifacts(args: argparse.Namespace) -> int:
    try:
        actual_env = _parse_actual_env(args.actual) if args.actual else None
    except ValueError as exc:
        print_payload(
            error_payload(
                code=CLI_USAGE_ERROR,
                message=str(exc),
                command="config artifacts",
                exit_code=2,
            ),
            as_json=args.as_json,
        )
        return 2

    artifacts = render_deployment_artifacts(args.profile, args.target)
    payload = {
        "ok": True,
        "command": "config artifacts",
        **artifacts.to_dict(),
    }
    if actual_env is not None:
        report = check_profile_drift(args.profile, actual_env, role=args.role)
        payload["ok"] = not report.has_drift
        payload["drift"] = report.to_dict()
        payload["alerts"] = _drift_alerts(args.profile, report, role=args.role)
        if args.role is not None:
            payload["role"] = args.role
    print_payload(payload, as_json=args.as_json)
    return 1 if payload["ok"] is False else 0


def _handle_config_write_artifacts(args: argparse.Namespace) -> int:
    try:
        artifacts = render_deployment_bundle_artifacts(args.profile, args.target)
        written_files = _write_artifact_files(
            artifacts.files,
            output_dir=Path(args.output_dir),
        )
    except (OSError, ValueError) as exc:
        print_payload(
            error_payload(
                code=CLI_USAGE_ERROR,
                message=str(exc),
                command="config write-artifacts",
                exit_code=2,
            ),
            as_json=args.as_json,
        )
        return 2

    print_payload(
        {
            "ok": True,
            "command": "config write-artifacts",
            "profile": artifacts.profile,
            "target": artifacts.target,
            "output_dir": str(Path(args.output_dir)),
            "written_files": written_files,
        },
        as_json=args.as_json,
    )
    return 0


def _parse_actual_env(values: list[str]) -> dict[str, str]:
    if not values:
        return dict(os.environ)
    actual: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Actual config mapping must use KEY=VALUE format: {value}")
        key, item_value = value.split("=", 1)
        if not key:
            raise ValueError(f"Actual config mapping must use KEY=VALUE format: {value}")
        actual[key] = item_value
    return actual


def _write_artifact_files(files: list[DeploymentArtifact], *, output_dir: Path) -> list[str]:
    root = output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for artifact in files:
        relative = Path(*artifact.path.split("/"))
        target = (root / relative).resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"Artifact path escapes output directory: {artifact.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact.content, encoding="utf-8")
        written.append(artifact.path)
    return written


def _drift_alerts(
    profile: DeploymentMode,
    report: ConfigDriftReport,
    *,
    role: str | None,
) -> list[dict[str, object]]:
    return config_drift_alerts(
        profile=profile,
        has_drift=report.has_drift,
        missing_count=len(report.missing),
        mismatched_count=len(report.mismatched),
        role=role,
    )
