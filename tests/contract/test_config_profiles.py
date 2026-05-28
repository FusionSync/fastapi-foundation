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
