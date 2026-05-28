from __future__ import annotations

import ast
import importlib
import inspect
from dataclasses import dataclass, field
from importlib import util
from pathlib import Path

from fastapi.responses import FileResponse, StreamingResponse
from fastapi.routing import APIRoute

from core.apps.boundaries import check_public_api_boundaries
from core.apps.dependencies import validate_app_dependencies
from core.apps.module import AppModule, validate_app_module
from core.base import get_router_security_policy
from core.base.models import TenantScopedModel
from core.db.constraints import check_tenant_scoped_model
from core.exceptions.codes import validate_error_code_spec
from core.migrations.manifest import MigrationManifest
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
BINARY_RESPONSE_CLASSES = (FileResponse, StreamingResponse)


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
    _check_error_code_metadata(app_module, result)
    _check_admin_metadata(app_module, result)
    _check_background_handler_signatures(app_module, result)
    _check_lifecycle_hook_signatures(app_module, result)
    _check_model_constraints(app_module, result)
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
    _check_duplicate_app_error_codes(modules_by_result)
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
        return
    manifest_module_path = f"{app_module.migrations.path}.manifest"
    try:
        manifest_module = importlib.import_module(manifest_module_path)
    except ModuleNotFoundError as exc:
        if exc.name == manifest_module_path:
            return
        result.errors.append(
            f"migration metadata {manifest_module_path} cannot be imported: "
            f"{type(exc).__name__}: {exc}"
        )
        return
    except Exception as exc:
        result.errors.append(
            f"migration metadata {manifest_module_path} cannot be imported: "
            f"{type(exc).__name__}: {exc}"
        )
        return

    raw_manifests = getattr(
        manifest_module,
        "MIGRATIONS",
        getattr(manifest_module, "migrations", []),
    )
    if not isinstance(raw_manifests, list):
        result.errors.append(
            f"migration metadata {manifest_module_path} MIGRATIONS must be a list"
        )
        return
    seen: set[str] = set()
    for index, raw_manifest in enumerate(raw_manifests, start=1):
        if not isinstance(raw_manifest, MigrationManifest):
            result.errors.append(
                f"migration metadata {manifest_module_path} item #{index} "
                "must be MigrationManifest"
            )
            continue
        if raw_manifest.key in seen:
            result.errors.append(
                f"migration metadata {manifest_module_path} duplicate key {raw_manifest.key}"
            )
        seen.add(raw_manifest.key)
        if raw_manifest.app_label != app_module.label:
            result.errors.append(
                f"migration metadata {manifest_module_path} {raw_manifest.key} "
                f"app_label does not match app {app_module.label!r}"
            )
        for violation in raw_manifest.validate():
            result.errors.append(f"migration metadata {manifest_module_path} {violation}")


def _check_error_code_metadata(app_module: AppModule, result: AppCheckResult) -> None:
    seen: set[str] = set()
    for spec in app_module.error_codes:
        try:
            validate_error_code_spec(spec)
        except ValueError as exc:
            result.errors.append(f"error code {spec.code} invalid metadata: {exc}")
            continue
        if spec.code in seen:
            result.errors.append(f"duplicate app error code {spec.code}")
        seen.add(spec.code)
        if spec.owner_module != app_module.label:
            result.errors.append(
                f"error code {spec.code} owner_module must match app label "
                f"{app_module.label!r}"
            )


def _check_duplicate_app_error_codes(
    modules_by_result: list[tuple[AppCheckResult, AppModule]],
) -> None:
    declarations: dict[str, list[tuple[AppCheckResult, AppModule]]] = {}
    for result, app_module in modules_by_result:
        for spec in app_module.error_codes:
            declarations.setdefault(spec.code, []).append((result, app_module))

    for code, declared_by in declarations.items():
        labels = sorted({app_module.label for _, app_module in declared_by})
        if len(labels) <= 1:
            continue
        error = f"duplicate app error code {code} declared by apps: {', '.join(labels)}"
        for result, _ in declared_by:
            result.errors.append(error)


def _check_admin_metadata(app_module: AppModule, result: AppCheckResult) -> None:
    for spec in app_module.admin_models:
        _check_importable_path(
            spec.model_path,
            metadata_label=f"admin model {spec.admin_id} model_path",
            result=result,
        )
    for spec in app_module.admin_routes:
        _check_callable_path(
            spec.handler_path,
            metadata_label=f"admin route {spec.route_id} handler_path",
            result=result,
        )
    for spec in app_module.dashboard_widgets:
        _check_callable_path(
            spec.provider_path,
            metadata_label=f"dashboard widget {spec.widget_id} provider_path",
            result=result,
        )


def _check_importable_path(
    dotted_path: str,
    *,
    metadata_label: str,
    result: AppCheckResult,
) -> None:
    try:
        _load_dotted_attribute(dotted_path)
    except Exception as exc:
        result.errors.append(
            f"{metadata_label} {dotted_path} cannot be imported: "
            f"{type(exc).__name__}: {exc}"
        )


def _check_callable_path(
    dotted_path: str,
    *,
    metadata_label: str,
    result: AppCheckResult,
) -> None:
    try:
        handler = _load_dotted_attribute(dotted_path)
    except Exception as exc:
        result.errors.append(
            f"{metadata_label} {dotted_path} cannot be imported: "
            f"{type(exc).__name__}: {exc}"
        )
        return
    if not callable(handler):
        result.errors.append(f"{metadata_label} {dotted_path} must be callable")


def _load_dotted_attribute(dotted_path: str) -> object:
    module_path, separator, attribute = dotted_path.rpartition(".")
    if not separator or not module_path or not attribute:
        raise ValueError(f"Invalid dotted path: {dotted_path!r}")
    module = importlib.import_module(module_path)
    return getattr(module, attribute)


def _check_background_handler_signatures(
    app_module: AppModule,
    result: AppCheckResult,
) -> None:
    for spec in app_module.event_handlers:
        _check_envelope_handler_signature(
            spec.handler_path,
            handler_kind="event",
            result=result,
        )
    for spec in app_module.task_handlers:
        _check_envelope_handler_signature(
            spec.handler_path,
            handler_kind="task",
            result=result,
        )


def _check_envelope_handler_signature(
    handler_path: str,
    *,
    handler_kind: str,
    result: AppCheckResult,
) -> None:
    try:
        handler = _load_handler(handler_path)
    except Exception as exc:
        result.errors.append(
            f"{handler_kind} handler {handler_path} cannot be imported: "
            f"{type(exc).__name__}: {exc}"
        )
        return
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError) as exc:
        result.errors.append(
            f"{handler_kind} handler {handler_path} signature cannot be inspected: "
            f"{type(exc).__name__}: {exc}"
        )
        return
    parameters = list(signature.parameters.values())
    if len(parameters) != 1:
        result.errors.append(
            f"{handler_kind} handler {handler_path} must accept exactly one "
            "envelope argument"
        )
        return
    parameter = parameters[0]
    if parameter.kind not in {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }:
        result.errors.append(
            f"{handler_kind} handler {handler_path} must accept exactly one "
            "envelope argument"
        )


def _check_lifecycle_hook_signatures(app_module: AppModule, result: AppCheckResult) -> None:
    for spec in app_module.lifecycle_hooks:
        _check_context_handler_signature(
            spec.handler_path,
            hook_id=spec.hook_id,
            result=result,
        )


def _check_context_handler_signature(
    handler_path: str,
    *,
    hook_id: str,
    result: AppCheckResult,
) -> None:
    try:
        handler = _load_handler(handler_path)
    except Exception as exc:
        result.errors.append(
            f"lifecycle hook {hook_id} {handler_path} cannot be imported: "
            f"{type(exc).__name__}: {exc}"
        )
        return
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError) as exc:
        result.errors.append(
            f"lifecycle hook {hook_id} {handler_path} signature cannot be inspected: "
            f"{type(exc).__name__}: {exc}"
        )
        return
    parameters = list(signature.parameters.values())
    if len(parameters) != 1:
        result.errors.append(
            f"lifecycle hook {hook_id} {handler_path} must accept exactly one "
            "context argument"
        )
        return
    parameter = parameters[0]
    if parameter.kind not in {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    }:
        result.errors.append(
            f"lifecycle hook {hook_id} {handler_path} must accept exactly one "
            "context argument"
        )


def _load_handler(handler_path: str) -> object:
    module_path, separator, attribute = handler_path.rpartition(".")
    if not separator or not module_path or not attribute:
        raise ValueError(f"Invalid handler path: {handler_path!r}")
    module = importlib.import_module(module_path)
    handler = getattr(module, attribute, None)
    if not callable(handler):
        raise TypeError(f"{handler_path!r} must be callable")
    return handler


def _check_model_constraints(app_module: AppModule, result: AppCheckResult) -> None:
    for model_path in app_module.models:
        try:
            model_module = importlib.import_module(model_path)
        except Exception as exc:
            result.errors.append(f"{model_path}: model import failed: {type(exc).__name__}: {exc}")
            continue
        for _, model in inspect.getmembers(model_module, inspect.isclass):
            if model.__module__ != model_module.__name__:
                continue
            if model is TenantScopedModel or not issubclass(model, TenantScopedModel):
                continue
            if not hasattr(model, "__table__"):
                continue
            for violation in check_tenant_scoped_model(model):
                result.errors.append(
                    f"{model_path}.{model.__name__} tenant scoped constraint violation: "
                    f"{violation}"
                )


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
            if _has_explicit_binary_response_class(route):
                continue
            response_model = route.response_model
            if isinstance(response_model, type) and issubclass(
                response_model,
                (Envelope, ListEnvelope),
            ):
                continue
            result.errors.append(
                f"{route.path} route must declare response_model=Envelope[...] "
                "or ListEnvelope[...] unless it uses an explicit binary response_class"
            )


def _has_explicit_binary_response_class(route: APIRoute) -> bool:
    response_class = route.response_class
    return isinstance(response_class, type) and issubclass(
        response_class,
        BINARY_RESPONSE_CLASSES,
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
