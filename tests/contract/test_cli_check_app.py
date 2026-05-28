import json

from core.cli.main import main


def test_check_app_json_output(capsys) -> None:
    exit_code = main(["check-app", "apps.example_domain.module", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {
        "module_path": "apps.example_domain.module",
        "label": "example_domain",
        "version": "0.1.0",
        "ok": True,
        "errors": [],
        "warnings": [],
    }


def test_check_app_all_json_output(capsys) -> None:
    exit_code = main(
        [
            "check-app",
            "--all",
            "--installed-app",
            "apps.example_domain.module",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["apps"][0]["label"] == "example_domain"


def test_check_app_missing_module_path_has_stable_json_error(capsys) -> None:
    exit_code = main(["check-app", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {
        "ok": False,
        "error": "check-app requires module_path unless --all is used",
    }


def test_list_apps_json_output(capsys) -> None:
    exit_code = main(
        [
            "list-apps",
            "--installed-app",
            "apps.example_domain.module",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["apps"] == [
        {
            "label": "example_domain",
            "version": "0.1.0",
            "dependencies": [],
            "routers": 1,
            "permissions": [
                {"resource": "example", "action": "read", "scope": "tenant"},
                {"resource": "example", "action": "write", "scope": "tenant"},
            ],
        }
    ]


def test_list_apps_invalid_module_has_stable_json_error(capsys) -> None:
    exit_code = main(["list-apps", "--installed-app", "apps.missing.module", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["apps"] == []
    assert "apps.missing.module" in payload["error"]
