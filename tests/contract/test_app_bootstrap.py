import importlib
import json
import sys
from pathlib import Path

from core.apps.bootstrap import bootstrap_app
from core.apps.conformance import check_app
from core.cli.main import main


def test_bootstrap_app_writes_conformance_ready_backend_app(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target_root = tmp_path / "src"

    result = bootstrap_app("sales_ops", target_root=target_root)

    assert result.module_path == "apps.sales_ops.module"
    assert result.relative_files == [
        "apps/__init__.py",
        "apps/sales_ops/__init__.py",
        "apps/sales_ops/error_messages.py",
        "apps/sales_ops/errors.py",
        "apps/sales_ops/events.py",
        "apps/sales_ops/migrations/__init__.py",
        "apps/sales_ops/migrations/manifest.py",
        "apps/sales_ops/models.py",
        "apps/sales_ops/module.py",
        "apps/sales_ops/permissions.py",
        "apps/sales_ops/public_api.py",
        "apps/sales_ops/router.py",
        "apps/sales_ops/schemas.py",
        "apps/sales_ops/services.py",
        "apps/sales_ops/tasks.py",
        "apps/sales_ops/tests/test_sales_ops_contract.py",
    ]
    _assert_generated_app_contains_no_alternate_orm(target_root / "apps" / "sales_ops")
    module_text = (target_root / "apps" / "sales_ops" / "module.py").read_text(
        encoding="utf-8"
    )
    errors_text = (target_root / "apps" / "sales_ops" / "errors.py").read_text(
        encoding="utf-8"
    )
    messages_text = (target_root / "apps" / "sales_ops" / "error_messages.py").read_text(
        encoding="utf-8"
    )
    assert "error_codes=ERROR_CODES" in module_text
    assert "message_catalogs=MESSAGE_CATALOGS" in module_text
    assert "define_module_error_codes" in errors_text
    assert "define_module_message_catalogs" in messages_text

    monkeypatch.syspath_prepend(str(target_root))
    _clear_imported_app_modules("sales_ops")
    importlib.invalidate_caches()

    try:
        check_result = check_app(result.module_path)
    finally:
        _clear_imported_app_modules("sales_ops")

    assert check_result.ok is True
    assert check_result.errors == []


def test_bootstrap_app_cli_outputs_module_path_and_refuses_existing_target(
    tmp_path: Path,
    capsys,
) -> None:
    target_root = tmp_path / "src"

    exit_code = main(
        [
            "bootstrap-app",
            "billing_ops",
            "--target-root",
            str(target_root),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["module_path"] == "apps.billing_ops.module"
    assert "apps/billing_ops/module.py" in payload["files"]

    repeated_exit_code = main(
        [
            "bootstrap-app",
            "billing_ops",
            "--target-root",
            str(target_root),
            "--json",
        ]
    )

    repeated_payload = json.loads(capsys.readouterr().out)
    assert repeated_exit_code == 1
    assert repeated_payload["ok"] is False
    assert repeated_payload["command"] == "bootstrap-app"
    assert repeated_payload["error"]["code"] == "CLI_RUNTIME_ERROR"
    assert "already exists" in repeated_payload["error"]["message"]


def _assert_generated_app_contains_no_alternate_orm(app_dir: Path) -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in app_dir.rglob("*.py"))
    for marker in ("Tor" + "toise", "Ae" + "rich"):
        assert marker not in combined


def _clear_imported_app_modules(label: str) -> None:
    for module_name in list(sys.modules):
        if module_name == "apps" or module_name.startswith(f"apps.{label}"):
            sys.modules.pop(module_name, None)
