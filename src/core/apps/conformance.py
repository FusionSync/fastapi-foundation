from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass, field
from importlib import util
from pathlib import Path

from core.apps.module import AppModule, validate_app_module

REQUIRED_APP_FILES = (
    "module.py",
    "schemas.py",
    "models.py",
    "router.py",
    "services.py",
    "permissions.py",
)


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
    _check_public_api_boundaries(package_dir, module_path, app_module, result)
    return result


def check_apps(module_paths: list[str]) -> list[AppCheckResult]:
    results = [check_app(path) for path in module_paths]
    labels: dict[str, str] = {}
    for result in results:
        if not result.label:
            continue
        if result.label in labels:
            result.errors.append(
                f"duplicate app label {result.label!r}; first declared by {labels[result.label]}"
            )
        labels[result.label] = result.module_path

    known_labels = set(labels)
    for result in results:
        if not result.ok:
            continue
        imported = importlib.import_module(result.module_path)
        app_module: AppModule = imported.module
        missing = [
            dependency
            for dependency in app_module.dependencies
            if dependency not in known_labels
        ]
        if missing:
            result.errors.append(f"missing dependencies: {missing}")
    _check_dependency_cycles(results)
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


def _check_public_api_boundaries(
    package_dir: Path,
    module_path: str,
    app_module: AppModule,
    result: AppCheckResult,
) -> None:
    own_prefix = module_path.rsplit(".", 1)[0] if module_path.endswith(".module") else module_path
    for path in package_dir.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            result.errors.append(f"{path.name}: syntax error: {exc}")
            continue
        for node in ast.walk(tree):
            imported_names = _imported_names(node)
            for imported_name in imported_names:
                if not imported_name.startswith(("apps.", "platform_apps.")):
                    continue
                if _is_same_package(imported_name, own_prefix):
                    continue
                if _is_public_api_import(imported_name):
                    _check_declared_dependency(imported_name, app_module, path, package_dir, result)
                    continue
                result.errors.append(
                    f"{path.relative_to(package_dir)} imports non-public app module {imported_name}"
                )


def _imported_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        if node.level:
            return [node.module]
        return [f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"]
    return []


def _is_same_package(imported_name: str, own_prefix: str) -> bool:
    return imported_name == own_prefix or imported_name.startswith(f"{own_prefix}.")


def _is_public_api_import(imported_name: str) -> bool:
    parts = imported_name.split(".")
    return len(parts) >= 3 and parts[2] == "public_api"


def _check_declared_dependency(
    imported_name: str,
    app_module: AppModule,
    path: Path,
    package_dir: Path,
    result: AppCheckResult,
) -> None:
    parts = imported_name.split(".")
    if len(parts) < 2 or parts[0] != "apps":
        return
    dependency_label = parts[1]
    if dependency_label in app_module.dependencies:
        return
    result.errors.append(
        f"{path.relative_to(package_dir)} imports {dependency_label!r} public_api "
        "without declaring it in dependencies"
    )


def _check_dependency_cycles(results: list[AppCheckResult]) -> None:
    modules: dict[str, AppModule] = {}
    module_paths_by_label: dict[str, str] = {}
    for result in results:
        if not result.ok or not result.label:
            continue
        imported = importlib.import_module(result.module_path)
        module = validate_app_module(imported.module)
        modules[module.label] = module
        module_paths_by_label[module.label] = result.module_path

    graph = {label: list(module.dependencies) for label, module in modules.items()}
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(label: str) -> None:
        if label in visited:
            return
        if label in visiting:
            cycle_start = path.index(label)
            cycle = [*path[cycle_start:], label]
            target_label = path[cycle_start]
            target = next(result for result in results if result.label == target_label)
            target.errors.append(f"circular dependencies: {' -> '.join(cycle)}")
            return
        visiting.add(label)
        path.append(label)
        for dependency in graph[label]:
            if dependency in graph:
                visit(dependency)
        path.pop()
        visiting.remove(label)
        visited.add(label)

    for label in module_paths_by_label:
        visit(label)
