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
    _configure_local_defaults()

    from core.cli.main import main as core_main

    return core_main(_default_args(sys.argv[1:]))


def _configure_local_defaults() -> None:
    if "DEPENDENCIES__REDIS_URL" in os.environ:
        return
    if _env_file_declares("DEPENDENCIES__REDIS_URL"):
        return
    os.environ["DEPENDENCIES__REDIS_URL"] = "redis://127.0.0.1:6379/0"


def _env_file_declares(key: str) -> bool:
    env_file = ROOT / ".env"
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _value = stripped.split("=", 1)
        if name.strip() == key:
            return True
    return False


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
