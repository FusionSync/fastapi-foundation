import json

from core.apps import AppRegistry
from core.cli.main import main
from core.permissions import PermissionRegistry


def test_permission_registry_collects_app_module_permissions() -> None:
    app_registry = AppRegistry(["apps.example_domain.module"]).load()

    permission_registry = PermissionRegistry.from_app_registry(app_registry)

    assert permission_registry.errors == []
    assert [
        (permission.app_label, permission.spec.resource, permission.spec.action)
        for permission in permission_registry.permissions
    ] == [
        ("example_domain", "example", "read"),
        ("example_domain", "example", "write"),
    ]


def test_permissions_catalog_cli_outputs_stable_json(capsys) -> None:
    exit_code = main(
        [
            "permissions",
            "catalog",
            "--installed-app",
            "apps.example_domain.module",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["permissions"][0]["app_label"] == "example_domain"


def test_permissions_reconcile_cli_outputs_metadata_mode(capsys) -> None:
    exit_code = main(
        [
            "permissions",
            "reconcile",
            "--installed-app",
            "apps.example_domain.module",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["reconciled"] is True
    assert payload["mode"] == "metadata"
