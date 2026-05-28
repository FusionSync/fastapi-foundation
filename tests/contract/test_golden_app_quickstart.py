import json

from fastapi.testclient import TestClient

from core.app import create_app
from core.cli.main import main
from core.config import Settings


def test_golden_app_quickstart_validation(capsys) -> None:
    assert _run_json(capsys, ["check-config", "--profile", "local", "--json"])["ok"] is True
    assert (
        _run_json(capsys, ["check-app", "apps.example_domain.module", "--json"])["ok"]
        is True
    )
    app_list = _run_json(
        capsys,
        ["list-apps", "--installed-app", "apps.example_domain.module", "--json"],
    )
    assert app_list["apps"][0]["label"] == "example_domain"
    assert (
        _run_json(
            capsys,
            [
                "permissions",
                "catalog",
                "--installed-app",
                "apps.example_domain.module",
                "--json",
            ],
        )["ok"]
        is True
    )
    assert (
        _run_json(
            capsys,
            ["migrate", "plan", "--installed-app", "apps.example_domain.module", "--json"],
        )["ok"]
        is True
    )
    assert _run_json(capsys, ["smoke", "--profile", "local", "--json"])["ok"] is True

    client = TestClient(create_app(Settings(installed_apps=["apps.example_domain.module"])))
    response = client.get("/api/v1/examples/ping")
    assert response.status_code == 200
    assert response.json()["data"] == {"app": "example_domain", "status": "ready"}


def _run_json(capsys, argv: list[str]) -> dict[str, object]:
    exit_code = main(argv)
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    return payload
