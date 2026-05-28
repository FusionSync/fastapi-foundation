from __future__ import annotations

import importlib
from collections.abc import Iterable
from dataclasses import dataclass, field

from fastapi import APIRouter

from core.apps.capabilities import DEFAULT_RUNTIME_CAPABILITIES
from core.apps.dependencies import validate_app_dependencies
from core.apps.module import AppModule, validate_app_module
from core.exceptions.base import AppError
from core.exceptions.codes import register_error_codes, validate_error_code_spec
from core.messages import register_message_catalogs

CORE_FRAMEWORK_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class AppModuleDiagnostic:
    module_path: str
    label: str | None = None
    version: str | None = None
    status: str = "pending"
    dependencies: list[str] = field(default_factory=list)
    min_core_version: str | None = None
    required_capabilities: list[str] = field(default_factory=list)
    provided_capabilities: list[str] = field(default_factory=list)
    core_version_compatible: bool = True
    missing_capabilities: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "module_path": self.module_path,
            "label": self.label,
            "version": self.version,
            "status": self.status,
            "dependencies": self.dependencies,
            "min_core_version": self.min_core_version,
            "required_capabilities": self.required_capabilities,
            "provided_capabilities": self.provided_capabilities,
            "core_version_compatible": self.core_version_compatible,
            "missing_capabilities": self.missing_capabilities,
            "errors": self.errors,
        }


@dataclass(frozen=True, slots=True)
class AppRegistryDiagnostics:
    ok: bool
    core_version: str
    runtime_capabilities: list[str]
    load_order: list[str] = field(default_factory=list)
    modules: list[AppModuleDiagnostic] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "core_version": self.core_version,
            "runtime_capabilities": self.runtime_capabilities,
            "load_order": self.load_order,
            "modules": [module.to_dict() for module in self.modules],
            "errors": self.errors,
        }


class AppRegistry:
    def __init__(
        self,
        installed_apps: Iterable[str] = (),
        *,
        core_version: str = CORE_FRAMEWORK_VERSION,
        runtime_capabilities: Iterable[str] | None = None,
    ) -> None:
        self.installed_apps = list(installed_apps)
        self.core_version = core_version
        self.runtime_capabilities = set(
            DEFAULT_RUNTIME_CAPABILITIES
            if runtime_capabilities is None
            else runtime_capabilities
        )
        self.modules: list[AppModule] = []
        self.diagnostics = AppRegistryDiagnostics(
            ok=True,
            core_version=self.core_version,
            runtime_capabilities=sorted(self.runtime_capabilities),
        )

    def load(self) -> AppRegistry:
        loaded: list[AppModule] = []
        loaded_entries: list[tuple[str, AppModule]] = []
        diagnostics: list[AppModuleDiagnostic] = []

        for module_path in self.installed_apps:
            try:
                imported = importlib.import_module(module_path)
            except Exception as exc:
                self._set_diagnostics(
                    modules=[
                        *diagnostics,
                        AppModuleDiagnostic(
                            module_path=module_path,
                            status="error",
                            errors=[f"ImportError: {exc}"],
                        ),
                    ],
                    errors=[f"Failed to import app module {module_path!r}: {exc}"],
                )
                raise ImportError(f"Failed to import app module {module_path!r}: {exc}") from exc
            try:
                app_module = validate_app_module(imported.module)
            except AttributeError as exc:
                self._set_diagnostics(
                    modules=[
                        *diagnostics,
                        AppModuleDiagnostic(
                            module_path=module_path,
                            status="error",
                            errors=["module attribute missing"],
                        ),
                    ],
                    errors=[f"App module {module_path!r} must expose `module`"],
                )
                raise ValueError(f"App module {module_path!r} must expose `module`") from exc
            except Exception as exc:
                self._set_diagnostics(
                    modules=[
                        *diagnostics,
                        AppModuleDiagnostic(
                            module_path=module_path,
                            status="error",
                            errors=[f"{type(exc).__name__}: {exc}"],
                        ),
                    ],
                    errors=[f"Invalid app module {module_path!r}: {exc}"],
                )
                raise ValueError(f"Invalid app module {module_path!r}: {exc}") from exc
            loaded.append(app_module)
            loaded_entries.append((module_path, app_module))
            diagnostics.append(self._module_diagnostic(module_path, app_module))

        validation = validate_app_dependencies(loaded)
        if not validation.ok:
            self._set_diagnostics(modules=diagnostics, errors=validation.errors)
            raise ValueError("; ".join(validation.errors))
        diagnostics = [
            self._module_diagnostic(module_path, app_module)
            for module_path, app_module in loaded_entries
        ]
        gate_errors = self._compatibility_errors(loaded)
        diagnostics = [
            self._module_diagnostic(
                module_path,
                app_module,
                status="blocked" if _module_has_gate_error(app_module, gate_errors) else "loaded",
                errors=_module_gate_errors(app_module, gate_errors),
            )
            for module_path, app_module in loaded_entries
        ]
        if gate_errors:
            self._set_diagnostics(
                modules=diagnostics,
                load_order=[module.label for module in validation.ordered_modules],
                errors=gate_errors,
            )
            raise ValueError("; ".join(gate_errors))
        try:
            self._register_app_error_codes(validation.ordered_modules)
            self._register_app_message_catalogs(validation.ordered_modules)
        except ValueError as exc:
            self._set_diagnostics(
                modules=diagnostics,
                load_order=[module.label for module in validation.ordered_modules],
                errors=[str(exc)],
            )
            raise
        self.modules = validation.ordered_modules
        self._set_diagnostics(
            modules=diagnostics,
            load_order=[module.label for module in self.modules],
            errors=[],
        )
        return self

    @property
    def routers(self) -> list[APIRouter]:
        return [router for module in self.modules for router in module.routers]

    def _set_diagnostics(
        self,
        *,
        modules: list[AppModuleDiagnostic],
        errors: list[str],
        load_order: list[str] | None = None,
    ) -> None:
        self.diagnostics = AppRegistryDiagnostics(
            ok=not errors,
            core_version=self.core_version,
            runtime_capabilities=sorted(self.runtime_capabilities),
            load_order=load_order or [],
            modules=modules,
            errors=errors,
        )

    def _module_diagnostic(
        self,
        module_path: str,
        module: AppModule,
        *,
        status: str = "loaded",
        errors: list[str] | None = None,
    ) -> AppModuleDiagnostic:
        missing_capabilities = sorted(
            set(module.required_capabilities) - self.runtime_capabilities
        )
        return AppModuleDiagnostic(
            module_path=module_path,
            label=module.label,
            version=module.version,
            status=status,
            dependencies=list(module.dependencies),
            min_core_version=module.min_core_version,
            required_capabilities=list(module.required_capabilities),
            provided_capabilities=list(module.provided_capabilities),
            core_version_compatible=_is_core_version_compatible(
                self.core_version,
                module.min_core_version,
            ),
            missing_capabilities=missing_capabilities,
            errors=errors or [],
        )

    def _compatibility_errors(self, modules: list[AppModule]) -> list[str]:
        errors: list[str] = []
        for module in modules:
            if not _is_core_version_compatible(self.core_version, module.min_core_version):
                errors.append(
                    f"App {module.label!r} requires core >= {module.min_core_version} "
                    f"but runtime core is {self.core_version}"
                )
            missing_capabilities = sorted(
                set(module.required_capabilities) - self.runtime_capabilities
            )
            if missing_capabilities:
                errors.append(
                    f"App {module.label!r} missing capabilities: "
                    f"{', '.join(missing_capabilities)}"
                )
        errors.extend(_error_code_gate_errors(modules))
        errors.extend(_message_catalog_gate_errors(modules))
        return errors

    def _register_app_error_codes(self, modules: list[AppModule]) -> None:
        specs = [spec for module in modules for spec in module.error_codes]
        if specs:
            register_error_codes(*specs)

    def _register_app_message_catalogs(self, modules: list[AppModule]) -> None:
        catalogs = [catalog for module in modules for catalog in module.message_catalogs]
        if catalogs:
            try:
                register_message_catalogs(*catalogs)
            except AppError as exc:
                raise ValueError(str(exc)) from exc


def _is_core_version_compatible(core_version: str, min_core_version: str | None) -> bool:
    if min_core_version is None:
        return True
    return _parse_version(core_version) >= _parse_version(min_core_version)


def _parse_version(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    normalized = [int(part) for part in parts]
    while len(normalized) < 3:
        normalized.append(0)
    return tuple(normalized[:3])  # type: ignore[return-value]


def _module_has_gate_error(module: AppModule, errors: list[str]) -> bool:
    return any(f"App {module.label!r} " in error for error in errors)


def _module_gate_errors(module: AppModule, errors: list[str]) -> list[str]:
    return [error for error in errors if f"App {module.label!r} " in error]


def _error_code_gate_errors(modules: list[AppModule]) -> list[str]:
    errors: list[str] = []
    declarations: dict[str, list[str]] = {}
    for module in modules:
        seen_by_module: set[str] = set()
        for spec in module.error_codes:
            try:
                validate_error_code_spec(spec)
            except ValueError as exc:
                errors.append(f"App {module.label!r} error code {spec.code}: {exc}")
                continue
            if spec.code in seen_by_module:
                errors.append(f"App {module.label!r} duplicate app error code {spec.code}")
            seen_by_module.add(spec.code)
            if spec.owner_module != module.label:
                errors.append(
                    f"App {module.label!r} error code {spec.code} owner_module "
                    "must match app label"
                )
            declarations.setdefault(spec.code, []).append(module.label)

    for code, labels in declarations.items():
        unique_labels = sorted(set(labels))
        if len(unique_labels) <= 1:
            continue
        declared_by = ", ".join(unique_labels)
        for label in unique_labels:
            errors.append(
                f"App {label!r} duplicate app error code {code} declared by apps: "
                f"{declared_by}"
            )
    return errors


def _message_catalog_gate_errors(modules: list[AppModule]) -> list[str]:
    errors: list[str] = []
    for module in modules:
        specs_by_code = {spec.code: spec for spec in module.error_codes}
        seen_by_locale: set[tuple[str, str]] = set()
        for catalog in module.message_catalogs:
            if catalog.owner_module != module.label:
                errors.append(
                    f"App {module.label!r} message catalog owner_module must match app label"
                )
            for code in catalog.messages:
                locale_code = (catalog.locale, code)
                if locale_code in seen_by_locale:
                    errors.append(
                        f"App {module.label!r} duplicate message catalog code {code} "
                        f"in locale {catalog.locale}"
                    )
                seen_by_locale.add(locale_code)
                spec = specs_by_code.get(code)
                if spec is None:
                    errors.append(
                        f"App {module.label!r} message catalog {catalog.locale} code {code} "
                        "must be declared in AppModule.error_codes"
                    )
                    continue
                if spec.deprecated:
                    errors.append(
                        f"App {module.label!r} message catalog {catalog.locale} code {code} "
                        "cannot target deprecated error code"
                    )
    return errors
