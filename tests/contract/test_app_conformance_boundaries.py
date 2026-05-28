import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

from core.apps.conformance import check_app, check_apps


@pytest.fixture
def isolated_apps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _purge_imported_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    _purge_imported_apps()


def test_check_app_rejects_missing_standard_file(isolated_apps: Path) -> None:
    _write_app(isolated_apps, "missing_schema")
    (isolated_apps / "apps" / "missing_schema" / "schemas.py").unlink()

    result = check_app("apps.missing_schema.module")

    assert result.ok is False
    assert "missing required file: schemas.py" in result.errors


def test_check_app_rejects_cross_app_internal_import(isolated_apps: Path) -> None:
    _write_app(isolated_apps, "other_domain")
    _write_app(
        isolated_apps,
        "example_domain",
        service_import="from apps.other_domain.models import X",
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is False
    assert any(
        "imports non-public app module apps.other_domain.models.X" in error
        for error in result.errors
    )


def test_check_app_does_not_allow_prefix_collision(isolated_apps: Path) -> None:
    _write_app(isolated_apps, "foobar")
    _write_app(isolated_apps, "foo", service_import="from apps.foobar.models import X")

    result = check_app("apps.foo.module")

    assert result.ok is False
    assert any("apps.foobar.models.X" in error for error in result.errors)


def test_check_app_allows_declared_public_api_import(isolated_apps: Path) -> None:
    _write_app(isolated_apps, "other_domain")
    _write_app(
        isolated_apps,
        "example_domain",
        dependencies=["other_domain"],
        service_import="from apps.other_domain.public_api import OtherService",
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is True
    assert result.errors == []


def test_check_app_rejects_undeclared_public_api_dependency(isolated_apps: Path) -> None:
    _write_app(isolated_apps, "other_domain")
    _write_app(
        isolated_apps,
        "example_domain",
        service_import="from apps.other_domain.public_api import OtherService",
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is False
    assert any("without declaring it in dependencies" in error for error in result.errors)


def test_check_app_rejects_platform_app_internal_import(isolated_apps: Path) -> None:
    _write_platform_app(isolated_apps, "tenants")
    _write_app(
        isolated_apps,
        "example_domain",
        service_import="from platform_apps.tenants.models import Tenant",
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is False
    assert any("platform_apps.tenants.models.Tenant" in error for error in result.errors)


def test_check_app_allows_platform_public_api_import(isolated_apps: Path) -> None:
    _write_platform_app(isolated_apps, "accounts")
    _write_app(
        isolated_apps,
        "example_domain",
        service_import="from platform_apps.accounts.public_api import AccountService",
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is True
    assert result.errors == []


def test_check_apps_rejects_missing_dependency(isolated_apps: Path) -> None:
    _write_app(isolated_apps, "example_domain", dependencies=["missing"])

    result = check_apps(["apps.example_domain.module"])[0]

    assert result.ok is False
    assert "missing dependencies: ['missing']" in result.errors


def test_check_apps_rejects_circular_dependency(isolated_apps: Path) -> None:
    _write_app(isolated_apps, "alpha", dependencies=["beta"])
    _write_app(isolated_apps, "beta", dependencies=["alpha"])

    results = check_apps(["apps.alpha.module", "apps.beta.module"])

    assert any(
        "circular dependencies: alpha -> beta -> alpha" in error
        for result in results
        for error in result.errors
    )


def _write_app(
    root: Path,
    name: str,
    *,
    label: str | None = None,
    dependencies: Iterable[str] = (),
    service_import: str = "",
) -> None:
    app_dir = root / "apps" / name
    migrations_dir = app_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    (root / "apps" / "__init__.py").touch()
    _write(app_dir / "__init__.py", f"from apps.{name}.module import module\n")
    _write(migrations_dir / "__init__.py", "")
    _write(
        app_dir / "schemas.py",
        "from core.base import BaseSchema\n\nclass ExampleSchema(BaseSchema):\n    name: str\n",
    )
    _write(app_dir / "models.py", "class ExampleModel:\n    pass\n")
    _write(app_dir / "services.py", f"{service_import}\n\nclass ExampleService:\n    pass\n")
    _write(
        app_dir / "router.py",
        "from core.base import create_router\n\nrouter = create_router('/examples')\n",
    )
    _write(
        app_dir / "permissions.py",
        "from core.permissions import PermissionSpec\n\n"
        "PERMISSIONS = [PermissionSpec(resource='example', action='read')]\n",
    )
    _write(
        app_dir / "public_api.py",
        "class OtherService:\n    pass\n",
    )
    _write(
        app_dir / "module.py",
        "from core.apps import AppModule, MigrationSpec\n"
        f"from apps.{name}.permissions import PERMISSIONS\n"
        f"from apps.{name}.router import router\n\n"
        "module = AppModule(\n"
        f"    label={label or name!r},\n"
        "    version='0.1.0',\n"
        f"    dependencies={list(dependencies)!r},\n"
        "    routers=[router],\n"
        f"    models=['apps.{name}.models'],\n"
        f"    migrations=MigrationSpec(path='apps.{name}.migrations'),\n"
        "    permissions=PERMISSIONS,\n"
        f"    public_api=['apps.{name}.public_api'],\n"
        ")\n",
    )


def _write_platform_app(root: Path, name: str) -> None:
    app_dir = root / "platform_apps" / name
    app_dir.mkdir(parents=True)
    (root / "platform_apps" / "__init__.py").touch()
    _write(app_dir / "__init__.py", "")
    _write(app_dir / "models.py", "class Tenant:\n    pass\n")
    _write(app_dir / "public_api.py", "class AccountService:\n    pass\n")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _purge_imported_apps() -> None:
    for name in list(sys.modules):
        if name == "apps" or name.startswith("apps."):
            del sys.modules[name]
        if name == "platform_apps" or name.startswith("platform_apps."):
            del sys.modules[name]
