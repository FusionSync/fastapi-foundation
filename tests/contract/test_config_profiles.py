import json

from core.cli.main import main
from core.config import Settings
from core.operations import check_config


def test_private_profile_template_outputs_process_matrix(capsys) -> None:
    exit_code = main(["config", "template", "--profile", "private", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["command"] == "config template"
    assert payload["profile"] == "private"
    assert payload["env"]["APP__ENV"] == "private"
    assert payload["env"]["DATABASE__URL"].startswith("postgresql+asyncpg://")
    assert payload["env"]["SECURITY__JWT_SECRET_REF"] == "APP_JWT_SECRET"
    assert payload["env"]["OUTBOX_DISPATCHER__BATCH_SIZE"] == "20"
    assert payload["env"]["OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS"] == "1.0"
    assert "SECURITY__JWT_SECRET" not in payload["env"]
    assert set(payload["processes"]) == {
        "server",
        "worker",
        "scheduler",
        "outbox-dispatcher",
        "migrate",
    }
    assert payload["processes"]["server"]["command"] == (
        "core serve --run --host 0.0.0.0 --port 8000"
    )
    assert "--instance-id ${INSTANCE_ID}" in payload["processes"]["worker"]["command"]
    assert "--instance-id ${INSTANCE_ID}" in payload["processes"]["scheduler"]["command"]
    assert "--instance-id ${INSTANCE_ID}" in payload["processes"]["outbox-dispatcher"]["command"]
    assert (
        "--batch-size ${OUTBOX_DISPATCHER__BATCH_SIZE}"
        in payload["processes"]["outbox-dispatcher"]["command"]
    )
    assert (
        "--idle-sleep-seconds ${OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS}"
        in payload["processes"]["outbox-dispatcher"]["command"]
    )
    assert payload["processes"]["migrate"]["command"] == (
        "core migrate run --backup-ready --json"
    )
    assert payload["validation_commands"] == [
        "core check-config --profile private --json",
        "core config drift-check --profile private --json",
        "core serve --run --dry-run --json",
        "core migrate run --backup-ready --json",
        "core smoke --profile private --json",
    ]
    assert [item["category"] for item in payload["security_hardening"]] == [
        "csp",
        "cookie",
        "tls",
        "headers",
    ]
    assert all(item["required"] is True for item in payload["security_hardening"])
    assert any(
        item["category"] == "tls"
        and "Strict-Transport-Security" in item["evidence"]
        and "includeSubDomains" in item["evidence"]
        for item in payload["security_hardening"]
    )
    assert [panel["id"] for panel in payload["monitoring"]["dashboard_panels"]] == [
        "http_traffic",
        "process_health",
        "outbox_delivery",
        "release_safety",
    ]
    assert any(
        rule["id"] == "config_drift_detected"
        and rule["severity"] == "critical"
        for rule in payload["monitoring"]["alert_rules"]
    )


def test_cloud_profile_template_uses_standard_http_and_external_secret_ref(capsys) -> None:
    exit_code = main(["config", "template", "--profile", "cloud", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["env"]["APP__ENV"] == "cloud"
    assert payload["env"]["API__ERROR_HTTP_STATUS_MODE"] == "standard"
    assert payload["env"]["SECURITY__JWT_SECRET_REF"] == "APP_JWT_SECRET"
    assert payload["env"]["SECURITY__TRUSTED_HOSTS"] == '["api.example.com"]'
    assert payload["env"]["SECURITY__CORS_ORIGINS"] == '["https://console.example.com"]'
    assert payload["processes"]["server"]["replicas"] == "autoscale"
    assert payload["processes"]["worker"]["replicas"] == "autoscale"
    assert payload["processes"]["scheduler"]["replicas"] == 1
    assert any(
        item["category"] == "tls" and "preload" in item["evidence"]
        for item in payload["security_hardening"]
    )


def test_private_profile_config_check_accepts_external_secret_reference() -> None:
    result = check_config(
        "private",
        Settings(
            app={"env": "private"},
            database={
                "url": "postgresql+asyncpg://app:${DATABASE_PASSWORD}@postgres:5432/wps_bid"
            },
            security={
                "jwt_secret_ref": "APP_JWT_SECRET",
                "trusted_hosts": ["api.internal.example"],
            },
        ),
    )

    assert result.ok is True
    assert result.errors == []


def test_private_profile_drift_check_accepts_matching_env(capsys) -> None:
    exit_code = main(
        [
            "config",
            "drift-check",
            "--profile",
            "private",
            "--actual",
            "APP__ENV=private",
            "--actual",
            "DATABASE__URL=postgresql+asyncpg://app:runtime-password@postgres:5432/wps_bid",
            "--actual",
            "API__ERROR_HTTP_STATUS_MODE=standard",
            "--actual",
            "SECURITY__JWT_SECRET_REF=APP_JWT_SECRET",
            "--actual",
            'SECURITY__TRUSTED_HOSTS=["api.internal.example"]',
            "--actual",
            'SECURITY__CORS_ORIGINS=["https://console.internal.example"]',
            "--actual",
            "OBSERVABILITY__SERVICE_ROLE=server",
            "--actual",
            "OUTBOX_DISPATCHER__BATCH_SIZE=20",
            "--actual",
            "OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS=1.0",
            "--actual",
            "INSTALLED_APPS=[]",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["command"] == "config drift-check"
    assert payload["profile"] == "private"
    assert payload["drift"] == {
        "has_drift": False,
        "checked": [
            "APP__ENV",
            "DATABASE__URL",
            "API__ERROR_HTTP_STATUS_MODE",
            "SECURITY__JWT_SECRET_REF",
            "SECURITY__TRUSTED_HOSTS",
            "SECURITY__CORS_ORIGINS",
            "OBSERVABILITY__SERVICE_ROLE",
            "OUTBOX_DISPATCHER__BATCH_SIZE",
            "OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS",
            "INSTALLED_APPS",
        ],
        "missing": [],
        "mismatched": [],
    }


def test_private_profile_drift_check_accepts_worker_role_env(capsys) -> None:
    exit_code = main(
        [
            "config",
            "drift-check",
            "--profile",
            "private",
            "--role",
            "worker",
            "--actual",
            "APP__ENV=private",
            "--actual",
            "DATABASE__URL=postgresql+asyncpg://app:runtime-password@postgres:5432/wps_bid",
            "--actual",
            "API__ERROR_HTTP_STATUS_MODE=standard",
            "--actual",
            "SECURITY__JWT_SECRET_REF=APP_JWT_SECRET",
            "--actual",
            'SECURITY__TRUSTED_HOSTS=["api.internal.example"]',
            "--actual",
            'SECURITY__CORS_ORIGINS=["https://console.internal.example"]',
            "--actual",
            "OBSERVABILITY__SERVICE_ROLE=worker",
            "--actual",
            "OUTBOX_DISPATCHER__BATCH_SIZE=20",
            "--actual",
            "OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS=1.0",
            "--actual",
            "INSTALLED_APPS=[]",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["role"] == "worker"
    assert payload["drift"]["has_drift"] is False


def test_profile_drift_check_reports_missing_and_redacted_mismatch(capsys) -> None:
    exit_code = main(
        [
            "config",
            "drift-check",
            "--profile",
            "private",
            "--actual",
            "APP__ENV=local",
            "--actual",
            "DATABASE__URL=postgresql+asyncpg://app:super-secret@wrong-host:5432/wps_bid",
            "--actual",
            "API__ERROR_HTTP_STATUS_MODE=standard",
            "--json",
        ]
    )

    payload_text = capsys.readouterr().out
    payload = json.loads(payload_text)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["command"] == "config drift-check"
    assert payload["drift"]["has_drift"] is True
    assert payload["alerts"] == [
        {
            "name": "ConfigDriftDetected",
            "severity": "critical",
            "profile": "private",
            "role": None,
            "labels": {"profile": "private"},
            "annotations": {
                "summary": "Runtime configuration drift detected for private profile",
                "missing_count": "7",
                "mismatched_count": "2",
                "runbook": "core config drift-check --profile private --json",
            },
        }
    ]
    assert payload["drift"]["missing"][0] == {
        "key": "SECURITY__JWT_SECRET_REF",
        "expected": "APP_JWT_SECRET",
    }
    assert {
        "key": "APP__ENV",
        "expected": "private",
        "actual": "local",
    } in payload["drift"]["mismatched"]
    assert any(
        mismatch["key"] == "DATABASE__URL"
        and "wrong-host" in mismatch["actual"]
        and "***" in mismatch["actual"]
        for mismatch in payload["drift"]["mismatched"]
    )
    assert "super-secret" not in payload_text
    assert "super-secret" not in json.dumps(payload["alerts"])


def test_private_profile_renders_docker_compose_deployment_artifacts(capsys) -> None:
    exit_code = main(
        [
            "config",
            "artifacts",
            "--profile",
            "private",
            "--target",
            "docker-compose",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["command"] == "config artifacts"
    assert payload["profile"] == "private"
    assert payload["target"] == "docker-compose"
    assert payload["source_template_command"] == "core config template --profile private --json"
    assert payload["drift_check_command"] == "core config drift-check --profile private --json"
    assert payload["validation_commands"] == [
        "core check-config --profile private --json",
        "core config drift-check --profile private --json",
        "core serve --run --dry-run --json",
        "core migrate run --backup-ready --json",
        "core smoke --profile private --json",
    ]
    assert [file["path"] for file in payload["files"]] == ["docker-compose.private.yml"]

    compose = payload["files"][0]["content"]
    assert "services:" in compose
    assert "x-security-hardening:" in compose
    assert "category: \"csp\"" in compose
    assert "category: \"cookie\"" in compose
    assert "Strict-Transport-Security" in compose
    assert "server:" in compose
    assert "worker:" in compose
    assert "scheduler:" in compose
    assert "outbox-dispatcher:" in compose
    assert "migrate:" in compose
    assert 'APP__ENV: "private"' in compose
    assert "DATABASE__URL:" in compose
    assert "SECURITY__JWT_SECRET_REF:" in compose
    assert "SECURITY__JWT_SECRET:" not in compose
    assert 'command: ["sh", "-lc", "core serve --run --host 0.0.0.0 --port 8000"]' in compose
    assert 'OBSERVABILITY__SERVICE_ROLE: "server"' in compose
    assert 'OBSERVABILITY__SERVICE_ROLE: "worker"' in compose
    assert 'OBSERVABILITY__SERVICE_ROLE: "scheduler"' in compose
    assert 'OBSERVABILITY__SERVICE_ROLE: "outbox-dispatcher"' in compose
    assert 'OBSERVABILITY__SERVICE_ROLE: "migrate"' in compose
    assert "replicas: 2" in compose
    assert 'profiles: ["release"]' in compose


def test_cloud_profile_renders_helm_values_from_process_matrix(capsys) -> None:
    exit_code = main(
        [
            "config",
            "artifacts",
            "--profile",
            "cloud",
            "--target",
            "helm-values",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    values = payload["files"][0]["content"]
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["target"] == "helm-values"
    assert payload["files"][0]["path"] == "values.cloud.yaml"
    assert 'profile: "cloud"' in values
    assert "workloads:" in values
    assert "server:" in values
    assert 'replicas: "autoscale"' in values
    assert 'command: "core worker --run --instance-id ${INSTANCE_ID}"' in values
    assert 'API__ERROR_HTTP_STATUS_MODE: "standard"' in values
    assert 'SECURITY__JWT_SECRET_REF: "APP_JWT_SECRET"' in values
    assert "securityHardening:" in values
    assert 'category: "tls"' in values
    assert "preload" in values
    assert "monitoring:" in values
    assert 'id: "config_drift_detected"' in values
    assert 'metric: "config_drift_has_drift"' in values
    assert 'id: "cloud_http_5xx_rate_high"' in values
    assert "validationCommands:" in values
    assert '  - "core config drift-check --profile cloud --json"' in values


def test_deployment_artifacts_validate_actual_env_with_redacted_drift(capsys) -> None:
    exit_code = main(
        [
            "config",
            "artifacts",
            "--profile",
            "private",
            "--target",
            "systemd",
            "--actual",
            "APP__ENV=local",
            "--actual",
            "DATABASE__URL=postgresql+asyncpg://app:super-secret@wrong-host:5432/wps_bid",
            "--json",
        ]
    )

    payload_text = capsys.readouterr().out
    payload = json.loads(payload_text)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["command"] == "config artifacts"
    assert payload["target"] == "systemd"
    assert payload["drift"]["has_drift"] is True
    assert payload["alerts"][0]["name"] == "ConfigDriftDetected"
    assert payload["alerts"][0]["annotations"]["runbook"] == (
        "core config drift-check --profile private --json"
    )
    assert payload["drift"]["missing"][0] == {
        "key": "API__ERROR_HTTP_STATUS_MODE",
        "expected": "standard",
    }
    assert {
        "key": "APP__ENV",
        "expected": "private",
        "actual": "local",
    } in payload["drift"]["mismatched"]
    assert any(
        mismatch["key"] == "DATABASE__URL"
        and "wrong-host" in mismatch["actual"]
        and "***" in mismatch["actual"]
        for mismatch in payload["drift"]["mismatched"]
    )
    env_file = next(file for file in payload["files"] if file["path"].endswith(".env"))
    assert "# Security hardening checklist:" in env_file["content"]
    assert "# - category: csp" in env_file["content"]
    assert "#   control:" in env_file["content"]
    assert "#   required: True" in env_file["content"]
    assert "Strict-Transport-Security" in env_file["content"]
    assert "super-secret" not in payload_text
