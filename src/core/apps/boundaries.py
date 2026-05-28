from __future__ import annotations

import ast
from pathlib import Path

from core.apps.module import AppModule


def check_public_api_boundaries(
    package_dir: Path,
    module_path: str,
    app_module: AppModule,
) -> list[str]:
    errors: list[str] = []
    own_prefix = module_path.rsplit(".", 1)[0] if module_path.endswith(".module") else module_path
    for path in package_dir.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path.name}: syntax error: {exc}")
            continue
        for imported_name in _iter_imported_names(tree):
            if not imported_name.startswith(("apps.", "platform_apps.")):
                continue
            if _is_same_package(imported_name, own_prefix):
                continue
            if _is_public_api_import(imported_name):
                dependency_error = _declared_dependency_error(
                    imported_name,
                    app_module,
                    path,
                    package_dir,
                )
                if dependency_error:
                    errors.append(dependency_error)
                continue
            errors.append(
                f"{path.relative_to(package_dir)} imports non-public app module {imported_name}"
            )
    return errors


def _iter_imported_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        names.extend(_imported_names(node))
    return names


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


def _declared_dependency_error(
    imported_name: str,
    app_module: AppModule,
    path: Path,
    package_dir: Path,
) -> str | None:
    parts = imported_name.split(".")
    dependency_label = _dependency_label_for_public_api_import(parts)
    if dependency_label is None:
        return None
    if dependency_label in app_module.dependencies:
        return None
    return (
        f"{path.relative_to(package_dir)} imports {dependency_label!r} public_api "
        "without declaring it in dependencies"
    )


def _dependency_label_for_public_api_import(parts: list[str]) -> str | None:
    if len(parts) < 3:
        return None
    if parts[0] == "apps":
        return parts[1]
    if parts[0] == "platform_apps":
        return f"platform_{parts[1]}"
    return None
