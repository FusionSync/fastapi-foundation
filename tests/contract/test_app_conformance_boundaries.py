import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

import pytest

from core.apps.conformance import check_app, check_apps

REAL_APPS_DIR = Path(__file__).resolve().parents[2] / "src" / "apps"
REAL_PLATFORM_APPS_DIR = Path(__file__).resolve().parents[2] / "src" / "platform_apps"


@pytest.fixture
def isolated_apps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    real_app_modules = _snapshot_real_app_modules()
    _purge_imported_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    _purge_imported_apps()
    sys.modules.update(real_app_modules)


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


def test_check_app_rejects_invalid_background_handler_signatures(
    isolated_apps: Path,
) -> None:
    _write_app(
        isolated_apps,
        "example_domain",
        event_handler_body="def handle_created():\n    pass\n",
        task_handler_body="def refresh(envelope, extra):\n    return None\n",
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is False
    assert (
        "event handler apps.example_domain.events.handle_created must accept exactly "
        "one envelope argument"
    ) in result.errors
    assert (
        "task handler apps.example_domain.tasks.refresh must accept exactly one "
        "envelope argument"
    ) in result.errors


def test_check_app_accepts_valid_background_handler_signatures(
    isolated_apps: Path,
) -> None:
    _write_app(
        isolated_apps,
        "example_domain",
        event_handler_body="def handle_created(envelope):\n    return None\n",
        task_handler_body="async def refresh(envelope):\n    return {'ok': True}\n",
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is True
    assert result.errors == []


def test_check_app_rejects_route_permission_not_declared_by_app(
    isolated_apps: Path,
) -> None:
    _write_app(
        isolated_apps,
        "example_domain",
        route_permissions=("example:write",),
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is False
    assert (
        "route security permission example:write must be declared in AppModule.permissions"
        in result.errors
    )


def test_check_app_rejects_route_permission_with_invalid_format(
    isolated_apps: Path,
) -> None:
    _write_app(
        isolated_apps,
        "example_domain",
        route_permissions=("example.write",),
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is False
    assert (
        "route security permission example.write must use resource:action format"
        in result.errors
    )


def test_check_app_accepts_declared_error_codes(isolated_apps: Path) -> None:
    _write_app(
        isolated_apps,
        "example_domain",
        error_codes=(
            "[ErrorCodeSpec("
            "'EXAMPLE_NOT_READY', 409, 'example is not ready', "
            "owner_module='example_domain', details_schema={}, deprecated=False"
            ")]"
        ),
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is True
    assert result.errors == []


def test_check_app_rejects_tenant_repository_without_scoped_base(
    isolated_apps: Path,
) -> None:
    _write_app(
        isolated_apps,
        "unsafe_repository_domain",
        tenant_model=True,
        repository_body=(
            "from core.base import BaseRepository\n"
            "from apps.unsafe_repository_domain.models import ExampleRecord\n\n"
            "class ExampleRepository(BaseRepository[ExampleRecord]):\n"
            "    model = ExampleRecord\n"
        ),
    )

    result = check_app("apps.unsafe_repository_domain.module")

    assert result.ok is False
    assert (
        "repository.py:ExampleRepository must inherit TenantScopedRepository "
        "or CrossTenantRepository for tenant-scoped model ExampleRecord"
    ) in result.errors


def test_check_app_accepts_tenant_scoped_repository_base(isolated_apps: Path) -> None:
    _write_app(
        isolated_apps,
        "safe_repository_domain",
        tenant_model=True,
        repository_body=(
            "from core.base import TenantScopedRepository\n"
            "from apps.safe_repository_domain.models import ExampleRecord\n\n"
            "class ExampleRepository(TenantScopedRepository[ExampleRecord]):\n"
            "    model = ExampleRecord\n"
        ),
    )

    result = check_app("apps.safe_repository_domain.module")

    assert result.ok is True
    assert result.errors == []


def test_check_app_rejects_service_direct_tenant_scoped_query(
    isolated_apps: Path,
) -> None:
    _write_app(
        isolated_apps,
        "unsafe_service_query_domain",
        tenant_model=True,
        service_import=(
            "from sqlalchemy import select\n"
            "from apps.unsafe_service_query_domain.models import ExampleRecord"
        ),
        service_body=(
            "async def unsafe_query(session):\n"
            "    return await session.execute(select(ExampleRecord))\n"
        ),
    )

    result = check_app("apps.unsafe_service_query_domain.module")

    assert result.ok is False
    assert any(
        "services.py:" in error
        and "must not import SQLAlchemy in router/service" in error
        for error in result.errors
    )
    assert any(
        "services.py:" in error
        and "must not execute ORM queries in router/service" in error
        for error in result.errors
    )


def test_check_app_rejects_router_direct_tenant_scoped_query(
    isolated_apps: Path,
) -> None:
    _write_app(
        isolated_apps,
        "unsafe_router_query_domain",
        tenant_model=True,
        router_body=(
            "from sqlalchemy import select\n"
            "from apps.unsafe_router_query_domain.models import ExampleRecord\n"
            "from core.base import create_router\n"
            "from core.serialization import Envelope, ok\n\n"
            "router = create_router('/examples')\n\n"
            "@router.get('/unsafe', response_model=Envelope[dict])\n"
            "async def unsafe(session):\n"
            "    rows = await session.execute(select(ExampleRecord))\n"
            "    return ok({'count': len(rows.all())})\n"
        ),
    )

    result = check_app("apps.unsafe_router_query_domain.module")

    assert result.ok is False
    assert any(
        "router.py:" in error
        and "must not import SQLAlchemy in router/service" in error
        for error in result.errors
    )
    assert any(
        "router.py:" in error
        and "must not execute ORM queries in router/service" in error
        for error in result.errors
    )


def test_check_app_rejects_error_code_owner_mismatch(isolated_apps: Path) -> None:
    _write_app(
        isolated_apps,
        "example_domain",
        error_codes=(
            "[ErrorCodeSpec("
            "'EXAMPLE_NOT_READY', 409, 'example is not ready', "
            "owner_module='other_domain', details_schema={}, deprecated=False"
            ")]"
        ),
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is False
    assert (
        "error code EXAMPLE_NOT_READY owner_module must match app label 'example_domain'"
        in result.errors
    )


def test_check_app_accepts_declared_message_catalogs(isolated_apps: Path) -> None:
    _write_app(
        isolated_apps,
        "example_domain",
        error_codes=(
            "[ErrorCodeSpec("
            "'EXAMPLE_MESSAGE_READY', 409, 'example message ready', "
            "owner_module='example_domain', details_schema={}, deprecated=False"
            ")]"
        ),
        message_catalogs=(
            "[MessageCatalog("
            "locale='en-US', owner_module='example_domain', "
            "messages={'EXAMPLE_MESSAGE_READY': 'Example message ready'}"
            ")]"
        ),
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is True
    assert result.errors == []


def test_check_app_rejects_message_catalog_owner_mismatch(isolated_apps: Path) -> None:
    _write_app(
        isolated_apps,
        "example_domain",
        error_codes=(
            "[ErrorCodeSpec("
            "'EXAMPLE_MESSAGE_OWNER_MISMATCH', 409, 'owner mismatch', "
            "owner_module='example_domain', details_schema={}, deprecated=False"
            ")]"
        ),
        message_catalogs=(
            "[MessageCatalog("
            "locale='en-US', owner_module='other_domain', "
            "messages={'EXAMPLE_MESSAGE_OWNER_MISMATCH': 'Wrong owner'}"
            ")]"
        ),
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is False
    assert (
        "message catalog en-US owner_module must match app label 'example_domain'"
        in result.errors
    )


def test_check_app_rejects_message_catalog_for_unknown_or_deprecated_code(
    isolated_apps: Path,
) -> None:
    _write_app(
        isolated_apps,
        "example_domain",
        error_codes=(
            "[ErrorCodeSpec("
            "'EXAMPLE_MESSAGE_DEPRECATED', 410, 'deprecated', "
            "owner_module='example_domain', details_schema={}, deprecated=True"
            ")]"
        ),
        message_catalogs=(
            "[MessageCatalog("
            "locale='en-US', owner_module='example_domain', "
            "messages={"
            "'EXAMPLE_MESSAGE_UNKNOWN': 'Unknown', "
            "'EXAMPLE_MESSAGE_DEPRECATED': 'Deprecated'"
            "}"
            ")]"
        ),
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is False
    assert (
        "message catalog en-US code EXAMPLE_MESSAGE_UNKNOWN must be declared in "
        "AppModule.error_codes"
    ) in result.errors
    assert (
        "message catalog en-US code EXAMPLE_MESSAGE_DEPRECATED cannot target "
        "deprecated error code"
    ) in result.errors


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
        dependencies=["platform_accounts"],
        service_import="from platform_apps.accounts.public_api import AccountService",
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is True
    assert result.errors == []


def test_check_app_rejects_undeclared_platform_public_api_dependency(
    isolated_apps: Path,
) -> None:
    _write_platform_app(isolated_apps, "accounts")
    _write_app(
        isolated_apps,
        "example_domain",
        service_import="from platform_apps.accounts.public_api import AccountService",
    )

    result = check_app("apps.example_domain.module")

    assert result.ok is False
    assert any(
        "imports 'platform_accounts' public_api without declaring it in dependencies" in error
        for error in result.errors
    )


def test_check_apps_rejects_missing_dependency(isolated_apps: Path) -> None:
    _write_app(isolated_apps, "example_domain", dependencies=["missing"])

    result = check_apps(["apps.example_domain.module"])[0]

    assert result.ok is False
    assert "missing dependencies: ['missing']" in result.errors


def test_check_apps_rejects_duplicate_labels(isolated_apps: Path) -> None:
    _write_app(isolated_apps, "first_domain", label="shared")
    _write_app(isolated_apps, "second_domain", label="shared")

    results = check_apps(["apps.first_domain.module", "apps.second_domain.module"])

    assert any(
        "Duplicate app label: shared" in error
        for result in results
        for error in result.errors
    )


def test_check_apps_rejects_circular_dependency(isolated_apps: Path) -> None:
    _write_app(isolated_apps, "alpha", dependencies=["beta"])
    _write_app(isolated_apps, "beta", dependencies=["alpha"])

    results = check_apps(["apps.alpha.module", "apps.beta.module"])

    assert any(
        "circular dependencies: alpha -> beta -> alpha" in error
        for result in results
        for error in result.errors
    )


def test_check_apps_rejects_duplicate_declared_error_codes(isolated_apps: Path) -> None:
    shared_code = (
        "[ErrorCodeSpec("
        "'SHARED_APP_ERROR', 409, 'shared error', "
        "owner_module={owner!r}, details_schema={{}}, deprecated=False"
        ")]"
    )
    _write_app(
        isolated_apps,
        "first_domain",
        error_codes=shared_code.format(owner="first_domain"),
    )
    _write_app(
        isolated_apps,
        "second_domain",
        error_codes=shared_code.format(owner="second_domain"),
    )

    results = check_apps(["apps.first_domain.module", "apps.second_domain.module"])

    assert any(
        "duplicate app error code SHARED_APP_ERROR declared by apps: "
        "first_domain, second_domain" in error
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
    service_body: str = "",
    router_body: str | None = None,
    event_handler_body: str | None = None,
    task_handler_body: str | None = None,
    error_codes: str | None = None,
    message_catalogs: str | None = None,
    route_permissions: tuple[str, ...] = (),
    tenant_model: bool = False,
    repository_body: str | None = None,
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
    if tenant_model:
        table_name = f"{name}_{root.name}_records".replace("-", "_")
        _write(
            app_dir / "models.py",
            "from sqlalchemy import String\n"
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "from core.base.models import IdMixin, TenantScopedModel\n\n"
            "class ExampleRecord(IdMixin, TenantScopedModel):\n"
            f"    __tablename__ = {table_name!r}\n\n"
            "    name: Mapped[str] = mapped_column(String(64), nullable=False)\n",
        )
    else:
        _write(app_dir / "models.py", "class ExampleModel:\n    pass\n")
    _write(
        app_dir / "services.py",
        f"{service_import}\n\nclass ExampleService:\n    pass\n\n{service_body}",
    )
    if repository_body is not None:
        _write(app_dir / "repository.py", repository_body)
    permissions_arg = (
        f", permissions={list(route_permissions)!r}" if route_permissions else ""
    )
    _write(
        app_dir / "router.py",
        router_body
        or (
            "from core.base import create_router\n\n"
            f"router = create_router('/examples'{permissions_arg})\n"
        ),
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
    if event_handler_body is not None:
        _write(app_dir / "events.py", event_handler_body)
    if task_handler_body is not None:
        _write(app_dir / "tasks.py", task_handler_body)
    app_module_imports = ["AppModule", "MigrationSpec"]
    if event_handler_body is not None:
        app_module_imports.append("EventHandlerSpec")
    if task_handler_body is not None:
        app_module_imports.append("TaskHandlerSpec")
    error_code_import = "from core.exceptions import ErrorCodeSpec\n" if error_codes else ""
    message_catalog_import = (
        "from core.messages import MessageCatalog\n" if message_catalogs else ""
    )
    error_codes_arg = f"    error_codes={error_codes},\n" if error_codes else ""
    message_catalogs_arg = (
        f"    message_catalogs={message_catalogs},\n" if message_catalogs else ""
    )
    event_handlers = (
        "    event_handlers=[\n"
        "        EventHandlerSpec(\n"
        "            event_type='example.created',\n"
        "            event_version=1,\n"
        f"            handler_path='apps.{name}.events.handle_created',\n"
        "        )\n"
        "    ],\n"
        if event_handler_body is not None
        else ""
    )
    task_handlers = (
        "    task_handlers=[\n"
        "        TaskHandlerSpec(\n"
        "            task_type='example.refresh',\n"
        f"            handler_path='apps.{name}.tasks.refresh',\n"
        "        )\n"
        "    ],\n"
        if task_handler_body is not None
        else ""
    )
    _write(
        app_dir / "module.py",
        f"from core.apps import {', '.join(app_module_imports)}\n"
        f"{error_code_import}"
        f"{message_catalog_import}"
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
        f"{error_codes_arg}"
        f"{message_catalogs_arg}"
        f"{event_handlers}"
        f"{task_handlers}"
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
            module_file = getattr(sys.modules[name], "__file__", None)
            if module_file and Path(module_file).resolve().is_relative_to(REAL_PLATFORM_APPS_DIR):
                continue
            del sys.modules[name]


def _snapshot_real_app_modules() -> dict[str, ModuleType]:
    modules: dict[str, ModuleType] = {}
    for name, module in sys.modules.items():
        if name != "apps" and not name.startswith("apps."):
            continue
        module_file = getattr(module, "__file__", None)
        if module_file and Path(module_file).resolve().is_relative_to(REAL_APPS_DIR):
            modules[name] = module
    return modules
