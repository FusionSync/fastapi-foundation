from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from core.config.profiles import (
    ProfileTemplate,
    expected_profile_env,
    render_profile_template,
)
from core.config.settings import DeploymentMode

DeploymentArtifactTarget = Literal["docker-compose", "systemd", "helm-values"]


@dataclass(frozen=True, slots=True)
class DeploymentArtifact:
    path: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "content": self.content,
        }


@dataclass(frozen=True, slots=True)
class DeploymentArtifactSet:
    profile: DeploymentMode
    target: DeploymentArtifactTarget
    source_template_command: str
    drift_check_command: str
    validation_commands: list[str]
    files: list[DeploymentArtifact]

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "target": self.target,
            "source_template_command": self.source_template_command,
            "drift_check_command": self.drift_check_command,
            "validation_commands": self.validation_commands,
            "files": [file.to_dict() for file in self.files],
        }


def render_deployment_artifacts(
    profile: DeploymentMode,
    target: DeploymentArtifactTarget,
) -> DeploymentArtifactSet:
    template = render_profile_template(profile)
    renderers = {
        "docker-compose": _docker_compose_artifacts,
        "systemd": _systemd_artifacts,
        "helm-values": _helm_values_artifacts,
    }
    files = renderers[target](template)
    return DeploymentArtifactSet(
        profile=profile,
        target=target,
        source_template_command=f"core config template --profile {profile} --json",
        drift_check_command=f"core config drift-check --profile {profile} --json",
        validation_commands=list(template.validation_commands),
        files=files,
    )


def render_deployment_bundle_artifacts(
    profile: DeploymentMode,
    target: DeploymentArtifactTarget,
) -> DeploymentArtifactSet:
    artifacts = render_deployment_artifacts(profile, target)
    if target == "docker-compose":
        files = [
            DeploymentArtifact(path="Dockerfile", content=_dockerfile()),
            *artifacts.files,
        ]
    elif target == "helm-values":
        values = artifacts.files[0]
        files = [
            DeploymentArtifact(
                path="helm/fastapi-foundation/Chart.yaml",
                content=_helm_chart(),
            ),
            DeploymentArtifact(
                path=f"helm/fastapi-foundation/values.{profile}.yaml",
                content=values.content,
            ),
            DeploymentArtifact(
                path="helm/fastapi-foundation/templates/workload.yaml",
                content=_helm_workload_template(),
            ),
        ]
    else:
        files = artifacts.files
    return DeploymentArtifactSet(
        profile=profile,
        target=target,
        source_template_command=artifacts.source_template_command,
        drift_check_command=artifacts.drift_check_command,
        validation_commands=artifacts.validation_commands,
        files=files,
    )


def _docker_compose_artifacts(template: ProfileTemplate) -> list[DeploymentArtifact]:
    lines = [
        f"name: fastapi-foundation-{template.profile}",
        "x-profile-validation:",
        "  commands:",
    ]
    for command in template.validation_commands:
        lines.append(f"    - {_yaml_scalar(command)}")
    lines.extend(_yaml_hardening_items(template.security_hardening, key="x-security-hardening"))
    lines.extend(_yaml_monitoring_contract(template.monitoring, key="x-monitoring"))
    lines.append("services:")
    for role, process in template.processes.items():
        lines.extend(
            [
                f"  {role}:",
                '    image: "${APP_IMAGE}"',
                f"    command: [\"sh\", \"-lc\", {_yaml_scalar(process.command)}]",
            ]
        )
        if role == "migrate":
            lines.extend(['    profiles: ["release"]', '    restart: "no"'])
        else:
            lines.append("    restart: unless-stopped")
        lines.append("    environment:")
        lines.extend(_yaml_mapping(expected_profile_env(template.profile, role=role), indent=6))
        if isinstance(process.replicas, int):
            lines.extend(["    deploy:", f"      replicas: {process.replicas}"])
        else:
            lines.extend(["    deploy:", f"      x-autoscale: {_yaml_scalar(process.replicas)}"])
        if process.notes:
            lines.append("    labels:")
            for index, note in enumerate(process.notes, start=1):
                lines.append(f"      fastapi-foundation.note.{index}: {_yaml_scalar(note)}")
    return [
        DeploymentArtifact(
            path=f"docker-compose.{template.profile}.yml",
            content="\n".join(lines) + "\n",
        )
    ]


def _dockerfile() -> str:
    return "\n".join(
        [
            "FROM python:3.12-slim",
            "",
            "ENV PYTHONDONTWRITEBYTECODE=1",
            "ENV PYTHONUNBUFFERED=1",
            "",
            "WORKDIR /app",
            "RUN pip install --no-cache-dir uv",
            "",
            "COPY pyproject.toml uv.lock ./",
            "RUN uv sync --frozen --no-dev",
            "",
            "COPY src ./src",
            "COPY run.py ./run.py",
            "",
            "ENV PATH=/app/.venv/bin:$PATH",
            "ENV PYTHONPATH=/app/src",
            "",
            'CMD ["python", "-m", "core.cli.main", "serve", "--run"]',
            "",
        ]
    )


def _systemd_artifacts(template: ProfileTemplate) -> list[DeploymentArtifact]:
    files = [
        DeploymentArtifact(
            path=f"fastapi-foundation-{template.profile}.env",
            content=_env_file(
                template.env,
                hardening_items=template.security_hardening,
                monitoring=template.monitoring,
            ),
        )
    ]
    for role, process in template.processes.items():
        restart = "no" if role == "migrate" else "always"
        service_type = "oneshot" if role == "migrate" else "simple"
        lines = [
            "[Unit]",
            f"Description=FastAPI Foundation {template.profile} {role}",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            f"Type={service_type}",
            f"EnvironmentFile=/etc/fastapi-foundation/{template.profile}.env",
            f"Environment=OBSERVABILITY__SERVICE_ROLE={role}",
            f"ExecStart=/usr/bin/env {process.command}",
            f"Restart={restart}",
        ]
        if role != "migrate":
            lines.append("RestartSec=5")
        if process.notes:
            lines.extend(["", "# Notes:"])
            lines.extend(f"# - {note}" for note in process.notes)
        lines.extend(
            [
                "",
                "[Install]",
                "WantedBy=multi-user.target",
            ]
        )
        files.append(
            DeploymentArtifact(
                path=f"fastapi-foundation-{role}.service",
                content="\n".join(lines) + "\n",
            )
        )
    return files


def _helm_values_artifacts(template: ProfileTemplate) -> list[DeploymentArtifact]:
    lines = [
        f"profile: {_yaml_scalar(template.profile)}",
        "image:",
        '  repository: "${APP_IMAGE_REPOSITORY}"',
        '  tag: "${APP_IMAGE_TAG}"',
        "env:",
    ]
    lines.extend(_yaml_mapping(template.env, indent=2))
    lines.append("workloads:")
    for role, process in template.processes.items():
        lines.extend(
            [
                f"  {role}:",
                f"    command: {_yaml_scalar(process.command)}",
                f"    replicas: {_yaml_scalar(process.replicas)}",
                f"    kind: {_yaml_scalar('job' if role == 'migrate' else 'deployment')}",
                "    env:",
            ]
        )
        lines.extend(_yaml_mapping(expected_profile_env(template.profile, role=role), indent=6))
        if process.notes:
            lines.append("    notes:")
            lines.extend(f"      - {_yaml_scalar(note)}" for note in process.notes)
    lines.extend(_yaml_hardening_items(template.security_hardening, key="securityHardening"))
    lines.extend(_yaml_monitoring_contract(template.monitoring, key="monitoring"))
    lines.append("validationCommands:")
    for command in template.validation_commands:
        lines.append(f"  - {_yaml_scalar(command)}")
    return [
        DeploymentArtifact(
            path=f"values.{template.profile}.yaml",
            content="\n".join(lines) + "\n",
        )
    ]


def _helm_chart() -> str:
    return "\n".join(
        [
            "apiVersion: v2",
            "name: fastapi-foundation",
            "description: FastAPI Foundation deployment chart",
            "type: application",
            "version: 0.1.0",
            "appVersion: 0.1.0",
            "",
        ]
    )


def _helm_workload_template() -> str:
    return "\n".join(
        [
            "{{- range $role, $workload := .Values.workloads }}",
            "apiVersion: apps/v1",
            "kind: Deployment",
            "metadata:",
            '  name: {{ printf "%s-%s" $.Chart.Name $role | trunc 63 | trimSuffix "-" }}',
            "  labels:",
            "    app.kubernetes.io/name: {{ $.Chart.Name }}",
            "    app.kubernetes.io/component: {{ $role }}",
            "spec:",
            "  replicas: {{ default 1 $workload.replicas }}",
            "  selector:",
            "    matchLabels:",
            "      app.kubernetes.io/name: {{ $.Chart.Name }}",
            "      app.kubernetes.io/component: {{ $role }}",
            "  template:",
            "    metadata:",
            "      labels:",
            "        app.kubernetes.io/name: {{ $.Chart.Name }}",
            "        app.kubernetes.io/component: {{ $role }}",
            "    spec:",
            "      containers:",
            "        - name: {{ $role }}",
            "          image: {{ $.Values.image.repository }}:{{ $.Values.image.tag }}",
            "          command:",
            "            - sh",
            "            - -lc",
            "            - {{ $workload.command | quote }}",
            "          env:",
            "            {{- range $name, $value := $workload.env }}",
            "            - name: {{ $name }}",
            "              value: {{ $value | quote }}",
            "            {{- end }}",
            "---",
            "{{- end }}",
            "",
        ]
    )


def _env_file(
    env: dict[str, str],
    *,
    hardening_items: object = (),
    monitoring: object = None,
) -> str:
    lines = [f"{key}={value}" for key, value in env.items()]
    items = list(hardening_items)
    if items:
        lines.append("")
        lines.append("# Security hardening checklist:")
        for item in items:
            lines.append(f"# - category: {item.category}")
            lines.append(f"#   control: {item.control}")
            lines.append(f"#   required: {item.required}")
            lines.append(f"#   evidence: {item.evidence}")
    if monitoring is not None:
        lines.append("")
        lines.append("# Monitoring alert contract:")
        for rule in monitoring.alert_rules:
            lines.append(f"# - {rule.name}: {rule.expression}")
    return "\n".join(lines) + "\n"


def _yaml_mapping(values: dict[str, str], *, indent: int) -> list[str]:
    spaces = " " * indent
    return [f"{spaces}{key}: {_yaml_scalar(value)}" for key, value in values.items()]


def _yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))


def _yaml_hardening_items(items: object, *, key: str) -> list[str]:
    hardening_items = list(items)
    lines = [f"{key}:"]
    for item in hardening_items:
        lines.extend(
            [
                f"  - category: {_yaml_scalar(item.category)}",
                f"    control: {_yaml_scalar(item.control)}",
                f"    required: {_yaml_scalar(item.required)}",
                f"    evidence: {_yaml_scalar(item.evidence)}",
            ]
        )
    return lines


def _yaml_monitoring_contract(contract: object, *, key: str) -> list[str]:
    if contract is None:
        return []
    lines = [f"{key}:", "  dashboardPanels:"]
    for panel in contract.dashboard_panels:
        lines.extend(
            [
                f"    - id: {_yaml_scalar(panel.id)}",
                f"      title: {_yaml_scalar(panel.title)}",
                f"      description: {_yaml_scalar(panel.description)}",
                "      metrics:",
            ]
        )
        lines.extend(f"        - {_yaml_scalar(metric)}" for metric in panel.metrics)
    lines.append("  alertRules:")
    for rule in contract.alert_rules:
        lines.extend(
            [
                f"    - id: {_yaml_scalar(rule.id)}",
                f"      name: {_yaml_scalar(rule.name)}",
                f"      severity: {_yaml_scalar(rule.severity)}",
                f"      metric: {_yaml_scalar(rule.metric)}",
                f"      expression: {_yaml_scalar(rule.expression)}",
                f"      for: {_yaml_scalar(rule.for_duration)}",
                "      labels:",
            ]
        )
        lines.extend(f"        - {_yaml_scalar(label)}" for label in rule.labels)
        lines.append(f"      runbook: {_yaml_scalar(rule.runbook)}")
    return lines
