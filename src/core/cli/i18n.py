from __future__ import annotations

import argparse
from pathlib import Path

from core.apps import resolve_runtime_capabilities
from core.apps.registry import AppRegistry
from core.cli.common import exception_error_payload, installed_apps, print_payload
from core.config import get_settings


def register_i18n_commands(subparsers: argparse._SubParsersAction) -> None:
    i18n_parser = subparsers.add_parser("i18n")
    i18n_subparsers = i18n_parser.add_subparsers(dest="i18n_command", required=True)

    export_parser = i18n_subparsers.add_parser("export-babel")
    export_parser.add_argument("--installed-app", action="append", default=[])
    export_parser.add_argument("--output-dir", default="locales")
    export_parser.add_argument("--json", action="store_true", dest="as_json")
    export_parser.set_defaults(handler=_handle_export_babel)


def _handle_export_babel(args: argparse.Namespace) -> int:
    module_paths = installed_apps(args.installed_app)
    settings = get_settings()
    try:
        registry = AppRegistry(
            module_paths,
            runtime_capabilities=resolve_runtime_capabilities(settings),
        ).load()
        files = export_babel_catalogs(registry, output_dir=Path(args.output_dir))
    except Exception as exc:
        print_payload(
            exception_error_payload(exc, command="i18n export-babel", files=[]),
            as_json=args.as_json,
        )
        return 1

    print_payload(
        {
            "ok": True,
            "command": "i18n export-babel",
            "files": [str(path) for path in files],
        },
        as_json=args.as_json,
    )
    return 0


def export_babel_catalogs(registry: AppRegistry, *, output_dir: Path) -> list[Path]:
    grouped: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for module in registry.modules:
        for catalog in module.translation_catalogs:
            entries = grouped.setdefault((catalog.locale, catalog.domain), [])
            for source, translation in catalog.messages.items():
                entries.append((source, translation, module.label))

    written_files: list[Path] = []
    for (locale, domain), entries in sorted(grouped.items()):
        file_path = output_dir / locale / "LC_MESSAGES" / f"{domain}.po"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(_render_po(locale=locale, entries=entries), encoding="utf-8")
        written_files.append(file_path)
    return written_files


def _render_po(*, locale: str, entries: list[tuple[str, str, str]]) -> str:
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Content-Type: text/plain; charset=utf-8\\\\n"',
        f'"Language: {_escape_po(locale)}\\\\n"',
        "",
    ]
    for source, translation, owner_module in sorted(entries):
        lines.extend(
            [
                f"#. owner_module: {owner_module}",
                f'msgid "{_escape_po(source)}"',
                f'msgstr "{_escape_po(translation)}"',
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def _escape_po(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
