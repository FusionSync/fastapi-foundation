from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AlertSeverity = Literal["info", "warning", "critical", "page"]
DeploymentProfile = Literal["local", "private", "cloud"]


@dataclass(frozen=True, slots=True)
class DashboardPanel:
    id: str
    title: str
    description: str
    metrics: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "metrics": list(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class AlertRule:
    id: str
    name: str
    severity: AlertSeverity
    metric: str
    expression: str
    for_duration: str
    labels: tuple[str, ...]
    runbook: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "severity": self.severity,
            "metric": self.metric,
            "expression": self.expression,
            "for": self.for_duration,
            "labels": list(self.labels),
            "runbook": self.runbook,
        }


@dataclass(frozen=True, slots=True)
class MonitoringContract:
    profile: DeploymentProfile
    dashboard_panels: tuple[DashboardPanel, ...]
    alert_rules: tuple[AlertRule, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "dashboard_panels": [panel.to_dict() for panel in self.dashboard_panels],
            "alert_rules": [rule.to_dict() for rule in self.alert_rules],
        }


def monitoring_contract(profile: DeploymentProfile) -> MonitoringContract:
    return MonitoringContract(
        profile=profile,
        dashboard_panels=_dashboard_panels(),
        alert_rules=_alert_rules(profile),
    )


def config_drift_alerts(
    *,
    profile: DeploymentProfile,
    has_drift: bool,
    missing_count: int,
    mismatched_count: int,
    role: str | None = None,
) -> list[dict[str, object]]:
    if not has_drift:
        return []
    labels = {"profile": profile}
    if role is not None:
        labels["role"] = role
    runbook = f"core config drift-check --profile {profile}"
    if role is not None:
        runbook += f" --role {role}"
    runbook += " --json"
    subject = f"{profile} profile" if role is None else f"{profile} profile {role} role"
    return [
        {
            "name": "ConfigDriftDetected",
            "severity": "critical",
            "profile": profile,
            "role": role,
            "labels": labels,
            "annotations": {
                "summary": f"Runtime configuration drift detected for {subject}",
                "missing_count": str(missing_count),
                "mismatched_count": str(mismatched_count),
                "runbook": runbook,
            },
        }
    ]


def _dashboard_panels() -> tuple[DashboardPanel, ...]:
    return (
        DashboardPanel(
            id="http_traffic",
            title="HTTP traffic and errors",
            description="Request volume, latency, status classes, and app codes.",
            metrics=("http_requests_total", "http_request_duration_seconds"),
        ),
        DashboardPanel(
            id="process_health",
            title="Process health",
            description="Server, worker, scheduler, outbox-dispatcher, and migrate health.",
            metrics=("process_heartbeat_fresh", "process_health_ok"),
        ),
        DashboardPanel(
            id="outbox_delivery",
            title="Outbox delivery",
            description="Pending, publishing, dead-letter, and dispatch outcomes.",
            metrics=(
                "outbox_events_pending",
                "outbox_events_publishing",
                "outbox_events_dead_letter",
                "outbox_dispatch_events_total",
            ),
        ),
        DashboardPanel(
            id="release_safety",
            title="Release safety",
            description="Config drift, migration gates, backup readiness, and smoke checks.",
            metrics=(
                "config_drift_has_drift",
                "release_checkpoint_ok",
                "migration_preflight_total",
                "migration_apply_total",
            ),
        ),
    )


def _alert_rules(profile: DeploymentProfile) -> tuple[AlertRule, ...]:
    rules = [
        AlertRule(
            id="config_drift_detected",
            name="ConfigDriftDetected",
            severity="critical",
            metric="config_drift_has_drift",
            expression=f'config_drift_has_drift{{profile="{profile}"}} > 0',
            for_duration="0m",
            labels=("profile", "role"),
            runbook=f"core config drift-check --profile {profile} --json",
        ),
        AlertRule(
            id="process_heartbeat_stale",
            name="ProcessHeartbeatStale",
            severity="critical",
            metric="process_heartbeat_fresh",
            expression=f'process_heartbeat_fresh{{profile="{profile}"}} == 0',
            for_duration="2m",
            labels=("profile", "service_role", "instance_id"),
            runbook=f"core smoke --profile {profile} --json",
        ),
        AlertRule(
            id="outbox_dead_letters_present",
            name="OutboxDeadLettersPresent",
            severity="warning",
            metric="outbox_events_dead_letter",
            expression="outbox_events_dead_letter > 0",
            for_duration="5m",
            labels=("profile", "service_role"),
            runbook="core outbox dead-letter list --json",
        ),
        AlertRule(
            id="release_checkpoint_failed",
            name="ReleaseCheckpointFailed",
            severity="critical",
            metric="release_checkpoint_ok",
            expression=f'release_checkpoint_ok{{profile="{profile}"}} == 0',
            for_duration="0m",
            labels=("profile",),
            runbook=(
                f"core release checkpoint --profile {profile} "
                f"--artifact-target {_default_artifact_target(profile)} --json"
            ),
        ),
    ]
    if profile == "cloud":
        rules.append(
            AlertRule(
                id="cloud_http_5xx_rate_high",
                name="CloudHttp5xxRateHigh",
                severity="page",
                metric="http_requests_total",
                expression='rate(http_requests_total{status_class="5xx"}[5m]) > 0',
                for_duration="5m",
                labels=("profile", "service_role", "route"),
                runbook="core smoke --profile cloud --json",
            )
        )
    return tuple(rules)


def _default_artifact_target(profile: DeploymentProfile) -> str:
    return "helm-values" if profile == "cloud" else "docker-compose"
