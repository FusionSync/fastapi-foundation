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
            "INSTALLED_APPS",
        ],
        "missing": [],
        "mismatched": [],
    }


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
