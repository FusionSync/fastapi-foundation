import importlib
import json
import sys
from pathlib import Path

from core.cli.main import main


def test_i18n_export_babel_writes_po_files(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    target_root = tmp_path / "src"
    output_dir = tmp_path / "locales"
    _write_i18n_app(target_root, "orders")
    monkeypatch.syspath_prepend(str(target_root))
    importlib.invalidate_caches()

    exit_code = main(
        [
            "i18n",
            "export-babel",
            "--installed-app",
            "i18n_apps.orders.module",
            "--output-dir",
            str(output_dir),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    po_file = output_dir / "zh-CN" / "LC_MESSAGES" / "orders.po"
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["files"] == [str(po_file)]
    assert po_file.read_text(encoding="utf-8") == (
        'msgid ""\n'
        'msgstr ""\n'
        '"Content-Type: text/plain; charset=utf-8\\\\n"\n'
        '"Language: zh-CN\\\\n"\n'
        "\n"
        '#. owner_module: orders\n'
        'msgid "Order created"\n'
        'msgstr "订单已创建"\n'
        "\n"
    )


def _write_i18n_app(root: Path, name: str) -> None:
    app_dir = root / "i18n_apps" / name
    app_dir.mkdir(parents=True)
    (root / "i18n_apps" / "__init__.py").touch()
    _write(app_dir / "__init__.py", f"from i18n_apps.{name}.module import module\n")
    _write(
        app_dir / "module.py",
        "from core.apps import AppModule\n"
        "from core.messages import TranslationCatalog\n\n"
        "module = AppModule(\n"
        f"    label={name!r},\n"
        "    version='0.1.0',\n"
        "    translation_catalogs=[\n"
        "        TranslationCatalog(\n"
        "            locale='zh-CN',\n"
        f"            domain={name!r},\n"
        f"            owner_module={name!r},\n"
        "            messages={'Order created': '订单已创建'},\n"
        "        )\n"
        "    ],\n"
        ")\n",
    )


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    for module_name in list(sys.modules):
        if module_name == "i18n_apps" or module_name.startswith("i18n_apps.orders"):
            sys.modules.pop(module_name, None)
