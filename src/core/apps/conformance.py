from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass, field
from importlib import util
from pathlib import Path

from fastapi.routing import APIRoute

from core.apps.boundaries import check_public_api_boundaries
from core.apps.dependencies import validate_app_dependencies
from core.apps.module import AppModule, validate_app_module
from core.base import get_router_security_policy
from core.serialization import Envelope, ListEnvelope

REQUIRED_APP_FILES = (
    "module.py",
    "schemas.py",
    "models.py",
    "router.py",
    "services.py",
    "permissions.py",
)
HTTP_ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete", "options", "head"}


@dataclass(slots=True)
class AppCheckResult:
    module_path: str
    label: str | None = None
    version: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "module_path": self.module_path,
            "label": self.label,
            "version": self.version,
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def check_app(module_path: str) -> AppCheckResult:
    result = AppCheckResult(module_path=module_path)
    try:
        imported = importlib.import_module(module_path)
    except Exception as exc:
        result.errors.append(f"import failed: {type(exc).__name__}: {exc}")
        return result

    app_module = getattr(imported, "module", None)
    try:
        app_module = validate_app_module(app_module)
    except Exception as exc:
        result.errors.append(f"invalid AppModule: {exc}")
        return result

    result.label = app_module.label
    result.version = app_module.version
    if not app_module.permissions:
        result.errors.append("app must declare at least one permission")

    module_file = getattr(imported, "__file__", None)
    if not module_file:
        result.errors.append("module file cannot be resolved")
        return result

    package_dir = Path(module_file).resolve().parent
    _check_required_files(package_dir, result)
    _check_migration_metadata(app_module, result)
    _check_router_security(app_module, result)
    _check_router_response_envelopes(package_dir, result)
    _check_router_openapi_envelopes(app_module, result)
    result.errors.extend(check_public_api_boundaries(package_dir, module_path, app_module))
    return result


def check_apps(module_paths: list[str]) -> list[AppCheckResult]:
    results = [check_app(path) for path in module_paths]
    modules_by_result: list[tuple[AppCheckResult, AppModule]] = []
    results_by_label: dict[str, list[AppCheckResult]] = {}
    for result in results:
        if not result.label:
            continue
        app_module = _load_checked_app_module(result)
        if app_module is None:
            continue
        modules_by_result.append((result, app_module))
        results_by_label.setdefault(app_module.label, []).append(result)

    validation = validate_app_dependencies([module for _, module in modules_by_result])
    for label, errors in validation.errors_by_label.items():
        for result in results_by_label.get(label, []):
            result.errors.extend(errors)
    return results


def _check_required_files(package_dir: Path, result: AppCheckResult) -> None:
    for file_name in REQUIRED_APP_FILES:
        if not (package_dir / file_name).is_file():
            result.errors.append(f"missing required file: {file_name}")


def _check_migration_metadata(app_module: AppModule, result: AppCheckResult) -> None:
    if app_module.migrations is None:
        result.errors.append("app must declare migrations metadata")
        return
    if not app_module.migrations.path:
        result.errors.append("migrations.path must be non-empty")
        return
    if util.find_spec(app_module.migrations.path) is None:
        result.errors.append(f"migrations.path cannot be imported: {app_module.migrations.path}")


def _check_router_security(app_module: AppModule, result: AppCheckResult) -> None:
    for router in app_module.routers:
        policy = get_router_security_policy(router)
        if policy is None:
            result.errors.append("router must be created with core.base.create_router")
            continue
        if router.routes and not policy.public and not policy.auth_required:
            result.errors.append("non-public router must require authentication")
        if policy.tenant_required and not policy.auth_required:
            result.errors.append("tenant-scoped router cannot disable authentication")


def _check_router_response_envelopes(package_dir: Path, result: AppCheckResult) -> None:
    router_path = package_dir / "router.py"
    if not router_path.is_file():
        return
    try:
        tree = ast.parse(router_path.read_text(encoding="utf-8"), filename=str(router_path))
    except SyntaxError as exc:
        result.errors.append(f"router.py: syntax error: {exc}")
        return
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not _is_route_handler(node):
            continue
        for return_node in ast.walk(node):
            if isinstance(return_node, ast.Return) and isinstance(
                return_node.value,
                ast.Dict | ast.List,
            ):
                result.errors.append(
                    f"router.py:{node.name} route handler must return core response "
                    "envelope, not raw dict/list"
                )


def _check_router_openapi_envelopes(app_module: AppModule, result: AppCheckResult) -> None:
    for router in app_module.routers:
        for route in router.routes:
            if not isinstance(route, APIRoute) or not route.include_in_schema:
                continue
            response_model = route.response_model
            if isinstance(response_model, type) and issubclass(
                response_model,
                (Envelope, ListEnvelope),
            ):
                continue
            result.errors.append(
                f"{route.path} route must declare response_model=Envelope[...] "
                "or ListEnvelope[...]"
            )


def _is_route_handler(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if isinstance(func, ast.Attribute) and func.attr in HTTP_ROUTE_DECORATORS:
            return True
    return False


def _load_checked_app_module(result: AppCheckResult) -> AppModule | None:
    try:
        imported = importlib.import_module(result.module_path)
        return validate_app_module(imported.module)
    except Exception:
        return None
