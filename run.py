from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
DEFAULT_INSTALLED_APPS = [
    "platform_apps.accounts.module",
    "platform_apps.tenants.module",
    "platform_apps.files.module",
    "platform_apps.audit.module",
    "apps.example_domain.module",
]


def main() -> int:
    os.chdir(ROOT)
    (ROOT / "data").mkdir(exist_ok=True)
    sys.path.insert(0, str(SRC))

    from core.cli.main import main as core_main

    return core_main(_default_args(sys.argv[1:]))


def _default_args(args: list[str]) -> list[str]:
    if not args:
        return [
            "serve",
            "--run",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            *_installed_app_args(),
        ]
    if args[0] == "serve" and "--installed-app" not in args:
        return [*args, *_installed_app_args()]
    return args


def _installed_app_args() -> list[str]:
    return [
        item
        for module_path in DEFAULT_INSTALLED_APPS
        for item in ("--installed-app", module_path)
    ]


if __name__ == "__main__":
    raise SystemExit(main())
