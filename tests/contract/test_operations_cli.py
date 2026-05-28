import json
from datetime import UTC, datetime

from core.cli.main import main


def test_check_config_local_profile_passes(capsys) -> None:
    exit_code = main(["check-config", "--profile", "local", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["profile"] == "local"


def test_check_config_cloud_profile_blocks_default_local_settings(capsys) -> None:
    exit_code = main(["check-config", "--profile", "cloud", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert any("JWT_SECRET" in error for error in payload["errors"])
    assert any("PostgreSQL" in error for error in payload["errors"])


def test_process_role_commands_return_health_json(capsys) -> None:
    for command in ("serve", "worker", "scheduler", "outbox-dispatcher"):
        exit_code = main([command, "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert exit_code == 0
        assert payload["ok"] is True
        assert payload["command"] == command
        assert payload["checks"]["database_configured"] is True


def test_local_deployment_smoke_passes(capsys) -> None:
    exit_code = main(["smoke", "--profile", "local", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["checks"] == {
        "config": True,
        "server_health": True,
        "worker_health": True,
        "scheduler_health": True,
        "outbox_dispatcher_health": True,
        "migrate_health": True,
    }
    assert payload["role_health"]["server"]["checks"]["http_routes_configured"] is True
    assert payload["role_health"]["worker"]["details"]["task_provider"] == "sync"
    assert payload["role_health"]["outbox-dispatcher"]["checks"][
        "outbox_claim_loop_configured"
    ] is True


def test_backup_check_requires_timestamp_for_private_profile(capsys) -> None:
    exit_code = main(["backup-check", "--profile", "private", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert "latest_backup_at is required" in payload["errors"][0]


def test_backup_check_accepts_recent_backup(capsys) -> None:
    latest_backup_at = datetime.now(UTC).isoformat()

    exit_code = main(
        [
            "backup-check",
            "--profile",
            "private",
            "--latest-backup-at",
            latest_backup_at,
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
