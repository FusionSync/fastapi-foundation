from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter

from core.admin.specs import (
    AdminDashboardWidgetSpec,
    AdminModelSpec,
    AdminPermissionSpec,
    AdminRouteSpec,
)
from core.exceptions.codes import ErrorCodeSpec, validate_error_code_spec
from core.messages import MessageCatalog, TranslationCatalog
from core.permissions.specs import PermissionSpec

_LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SETTING_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
ScheduleTrigger = Literal["interval", "cron", "date", "manual"]
MisfirePolicy = Literal["skip", "run_once", "catch_up_limited"]
LifecyclePhase = Literal["startup", "shutdown"]
SettingValueType = Literal[
    "string",
    "int",
    "float",
    "bool",
    "json",
    "enum",
    "string_list",
]
SettingScope = Literal["platform", "tenant"]
SettingKind = Literal["config", "flag"]
SettingRiskLevel = Literal["low", "normal", "high", "critical"]
_LIFECYCLE_PHASES = {"startup", "shutdown"}
_EVENT_SCHEMA_FIELD_TYPES = {"str", "int", "float", "number", "bool", "dict", "list"}
_SETTING_VALUE_TYPES = {
    "string",
    "int",
    "float",
    "bool",
    "json",
    "enum",
    "string_list",
}
_SETTING_SCOPES = {"platform", "tenant"}
_SETTING_KINDS = {"config", "flag"}
_SETTING_RISK_LEVELS = {"low", "normal", "high", "critical"}


@dataclass(frozen=True, slots=True)
class MigrationSpec:
    path: str
    depends_on: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EventHandlerSpec:
    event_type: str
    event_version: int
    handler_path: str


@dataclass(frozen=True, slots=True)
class EventSchemaSpec:
    event_type: str
    event_version: int
    required_payload_fields: list[str] = field(default_factory=list)
    field_types: dict[str, str] = field(default_factory=dict)
    compatible_with: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "event_version": self.event_version,
            "required_payload_fields": self.required_payload_fields,
            "field_types": self.field_types,
            "compatible_with": self.compatible_with,
        }


@dataclass(frozen=True, slots=True)
class TaskHandlerSpec:
    task_type: str
    handler_path: str
    queue: str = "default"


@dataclass(frozen=True, slots=True)
class ScheduleSpec:
    schedule_id: str
    task_type: str
    trigger: ScheduleTrigger
    trigger_config: dict[str, str] = field(default_factory=dict)
    misfire_policy: MisfirePolicy = "skip"


@dataclass(frozen=True, slots=True)
class SettingSpec:
    module: str
    key: str
    value_type: SettingValueType
    default: Any
    scopes: tuple[SettingScope, ...]
    category: str
    description: str
    required: bool = False
    runtime_mutable: bool = True
    sensitive: bool = False
    secret_ref_only: bool = False
    risk_level: SettingRiskLevel = "normal"
    cache_ttl_seconds: int | None = None
    allowed_values: tuple[str, ...] = ()
    min_value: float | None = None
    max_value: float | None = None
    kind: SettingKind = "config"
    deprecated: bool = False

    def __post_init__(self) -> None:
        _validate_setting_spec(self)

    @property
    def full_key(self) -> str:
        return f"{self.module}.{self.key}"

    def validate_value(self, value: object) -> object:
        return _validate_setting_value(self, value)


@dataclass(frozen=True, slots=True)
class LifecycleHookSpec:
    hook_id: str
    phase: LifecyclePhase
    handler_path: str


@dataclass(frozen=True, slots=True)
class AppModule:
    label: str
    version: str
    dependencies: list[str] = field(default_factory=list)
    min_core_version: str | None = None
    required_capabilities: list[str] = field(default_factory=list)
    provided_capabilities: list[str] = field(default_factory=list)
    routers: list[APIRouter] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    migrations: MigrationSpec | None = None
    permissions: list[PermissionSpec] = field(default_factory=list)
    error_codes: list[ErrorCodeSpec] = field(default_factory=list)
    message_catalogs: list[MessageCatalog] = field(default_factory=list)
    translation_catalogs: list[TranslationCatalog] = field(default_factory=list)
    event_schemas: list[EventSchemaSpec] = field(default_factory=list)
    event_handlers: list[EventHandlerSpec] = field(default_factory=list)
    task_handlers: list[TaskHandlerSpec] = field(default_factory=list)
    schedules: list[ScheduleSpec] = field(default_factory=list)
    settings: list[SettingSpec] = field(default_factory=list)
    lifecycle_hooks: list[LifecycleHookSpec] = field(default_factory=list)
    auth_session_store: str | None = None
    public_api: list[str] = field(default_factory=list)
    admin_models: list[AdminModelSpec] = field(default_factory=list)
    admin_routes: list[AdminRouteSpec] = field(default_factory=list)
    dashboard_widgets: list[AdminDashboardWidgetSpec] = field(default_factory=list)
    admin_permissions: list[AdminPermissionSpec] = field(default_factory=list)


def validate_app_module(module: AppModule) -> AppModule:
    if not isinstance(module, AppModule):
        raise TypeError("module must be an AppModule instance")
    if not _LABEL_PATTERN.match(module.label):
        raise ValueError(f"Invalid app label: {module.label!r}")
    if not module.version:
        raise ValueError(f"App {module.label!r} must declare version")
    if module.min_core_version is not None:
        _validate_non_empty_path(
            module.min_core_version,
            f"App {module.label!r} min_core_version",
        )
    _validate_list(module.dependencies, f"App {module.label!r} dependencies")
    _validate_list(
        module.required_capabilities,
        f"App {module.label!r} required_capabilities",
    )
    _validate_list(
        module.provided_capabilities,
        f"App {module.label!r} provided_capabilities",
    )
    _validate_list(module.error_codes, f"App {module.label!r} error_codes")
    _validate_list(module.message_catalogs, f"App {module.label!r} message_catalogs")
    _validate_list(module.translation_catalogs, f"App {module.label!r} translation_catalogs")
    for dependency in module.dependencies:
        if not _LABEL_PATTERN.match(dependency):
            raise ValueError(f"App {module.label!r} has invalid dependency: {dependency!r}")
    if module.label in module.dependencies:
        raise ValueError(f"App {module.label!r} cannot depend on itself")
    for capability in module.required_capabilities:
        _validate_non_empty_path(
            capability,
            f"App {module.label!r} required_capability",
        )
    for capability in module.provided_capabilities:
        _validate_non_empty_path(
            capability,
            f"App {module.label!r} provided_capability",
        )
    for model in module.models:
        if not isinstance(model, str) or not model:
            raise TypeError(f"App {module.label!r} model path must be a non-empty string")
    for router in module.routers:
        if not isinstance(router, APIRouter):
            raise TypeError(f"App {module.label!r} router must be APIRouter")
    for permission in module.permissions:
        if not isinstance(permission, PermissionSpec):
            raise TypeError(f"App {module.label!r} permission must be PermissionSpec")
    for error_code in module.error_codes:
        if not isinstance(error_code, ErrorCodeSpec):
            raise TypeError(f"App {module.label!r} error_code must be ErrorCodeSpec")
        try:
            validate_error_code_spec(error_code)
        except ValueError as exc:
            raise ValueError(f"App {module.label!r} error_code {error_code.code}: {exc}") from exc
    for message_catalog in module.message_catalogs:
        if not isinstance(message_catalog, MessageCatalog):
            raise TypeError(f"App {module.label!r} message_catalog must be MessageCatalog")
    for translation_catalog in module.translation_catalogs:
        if not isinstance(translation_catalog, TranslationCatalog):
            raise TypeError(
                f"App {module.label!r} translation_catalog must be TranslationCatalog"
            )
    for event_schema in module.event_schemas:
        if not isinstance(event_schema, EventSchemaSpec):
            raise TypeError(f"App {module.label!r} event_schema must be EventSchemaSpec")
        if event_schema.event_version < 1:
            raise ValueError(f"App {module.label!r} event_schema version must be positive")
        _validate_non_empty_path(
            event_schema.event_type,
            f"App {module.label!r} event_schema event_type",
        )
        _validate_list(
            event_schema.required_payload_fields,
            f"App {module.label!r} event_schema required_payload_fields",
        )
        for field_name in event_schema.required_payload_fields:
            _validate_non_empty_path(
                field_name,
                f"App {module.label!r} event_schema required_payload_fields item",
            )
        _validate_list(
            event_schema.compatible_with,
            f"App {module.label!r} event_schema compatible_with",
        )
        for version in event_schema.compatible_with:
            if not isinstance(version, int) or version < 1:
                raise ValueError(
                    f"App {module.label!r} event_schema compatible_with "
                    "versions must be positive integers"
                )
        if not isinstance(event_schema.field_types, dict):
            raise TypeError(f"App {module.label!r} event_schema field_types must be a dict")
        for field_name, field_type in event_schema.field_types.items():
            _validate_non_empty_path(
                field_name,
                f"App {module.label!r} event_schema field name",
            )
            _validate_non_empty_path(
                field_type,
                f"App {module.label!r} event_schema field type",
            )
            if field_type not in _EVENT_SCHEMA_FIELD_TYPES:
                raise ValueError(
                    f"App {module.label!r} event_schema unsupported field type: {field_type}"
                )
    for event_handler in module.event_handlers:
        if not isinstance(event_handler, EventHandlerSpec):
            raise TypeError(f"App {module.label!r} event_handler must be EventHandlerSpec")
        if event_handler.event_version < 1:
            raise ValueError(f"App {module.label!r} event_handler version must be positive")
        _validate_non_empty_path(
            event_handler.event_type,
            f"App {module.label!r} event_handler event_type",
        )
        _validate_non_empty_path(
            event_handler.handler_path,
            f"App {module.label!r} event_handler handler_path",
        )
    for task_handler in module.task_handlers:
        if not isinstance(task_handler, TaskHandlerSpec):
            raise TypeError(f"App {module.label!r} task_handler must be TaskHandlerSpec")
        _validate_non_empty_path(
            task_handler.task_type,
            f"App {module.label!r} task_handler task_type",
        )
        _validate_non_empty_path(
            task_handler.handler_path,
            f"App {module.label!r} task_handler handler_path",
        )
        _validate_non_empty_path(task_handler.queue, f"App {module.label!r} task_handler queue")
    for schedule in module.schedules:
        if not isinstance(schedule, ScheduleSpec):
            raise TypeError(f"App {module.label!r} schedule must be ScheduleSpec")
        _validate_non_empty_path(schedule.schedule_id, f"App {module.label!r} schedule_id")
        _validate_non_empty_path(schedule.task_type, f"App {module.label!r} schedule task_type")
    _validate_list(module.settings, f"App {module.label!r} settings")
    for setting in module.settings:
        if not isinstance(setting, SettingSpec):
            raise TypeError(f"App {module.label!r} setting must be SettingSpec")
    for lifecycle_hook in module.lifecycle_hooks:
        if not isinstance(lifecycle_hook, LifecycleHookSpec):
            raise TypeError(f"App {module.label!r} lifecycle_hook must be LifecycleHookSpec")
        _validate_non_empty_path(
            lifecycle_hook.hook_id,
            f"App {module.label!r} lifecycle_hook hook_id",
        )
        if lifecycle_hook.phase not in _LIFECYCLE_PHASES:
            raise ValueError(
                f"App {module.label!r} lifecycle_hook phase must be startup or shutdown"
            )
        _validate_non_empty_path(
            lifecycle_hook.handler_path,
            f"App {module.label!r} lifecycle_hook handler_path",
        )
    for public_api in module.public_api:
        if not isinstance(public_api, str) or not public_api:
            raise TypeError(f"App {module.label!r} public_api path must be a non-empty string")
    if module.auth_session_store is not None and (
        not isinstance(module.auth_session_store, str) or not module.auth_session_store
    ):
        raise TypeError(f"App {module.label!r} auth_session_store must be a non-empty string")
    for admin_model in module.admin_models:
        if not isinstance(admin_model, AdminModelSpec):
            raise TypeError(f"App {module.label!r} admin_model must be AdminModelSpec")
    for admin_route in module.admin_routes:
        if not isinstance(admin_route, AdminRouteSpec):
            raise TypeError(f"App {module.label!r} admin_route must be AdminRouteSpec")
    for dashboard_widget in module.dashboard_widgets:
        if not isinstance(dashboard_widget, AdminDashboardWidgetSpec):
            raise TypeError(
                f"App {module.label!r} dashboard_widget must be AdminDashboardWidgetSpec"
            )
    for admin_permission in module.admin_permissions:
        if not isinstance(admin_permission, AdminPermissionSpec):
            raise TypeError(
                f"App {module.label!r} admin_permission must be AdminPermissionSpec"
            )
    if module.migrations is not None and not isinstance(module.migrations, MigrationSpec):
        raise TypeError(f"App {module.label!r} migrations must be MigrationSpec")
    return module


def _validate_non_empty_path(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{label} must be a non-empty string")


def _validate_list(value: object, label: str) -> None:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be a list")


def _validate_setting_spec(spec: SettingSpec) -> None:
    if not _LABEL_PATTERN.fullmatch(spec.module):
        raise ValueError(f"setting module is invalid: {spec.module!r}")
    if not _SETTING_KEY_PATTERN.fullmatch(spec.key):
        raise ValueError(f"setting key is invalid: {spec.key!r}")
    if spec.value_type not in _SETTING_VALUE_TYPES:
        raise ValueError(f"setting value_type is invalid: {spec.value_type!r}")
    if not spec.scopes:
        raise ValueError("setting scopes cannot be empty")
    for scope in spec.scopes:
        if scope not in _SETTING_SCOPES:
            raise ValueError(f"setting scope is invalid: {scope!r}")
    if not spec.category.strip():
        raise ValueError("setting category is required")
    if not spec.description.strip():
        raise ValueError("setting description is required")
    if spec.risk_level not in _SETTING_RISK_LEVELS:
        raise ValueError(f"setting risk_level is invalid: {spec.risk_level!r}")
    if spec.kind not in _SETTING_KINDS:
        raise ValueError(f"setting kind is invalid: {spec.kind!r}")
    if spec.cache_ttl_seconds is not None and spec.cache_ttl_seconds < 0:
        raise ValueError("setting cache_ttl_seconds must be non-negative")
    _validate_setting_value(spec, spec.default, field_name="default")


def _validate_setting_value(
    spec: SettingSpec,
    value: object,
    *,
    field_name: str = "value",
) -> object:
    if spec.secret_ref_only:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"setting {field_name} must be a non-empty secret reference")
        return value
    if spec.value_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"setting {field_name} must be a string")
        return value
    if spec.value_type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"setting {field_name} must be an int")
        _validate_numeric_bounds(spec, float(value), field_name)
        return value
    if spec.value_type == "float":
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise ValueError(f"setting {field_name} must be a number")
        _validate_numeric_bounds(spec, float(value), field_name)
        return value
    if spec.value_type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"setting {field_name} must be a bool")
        return value
    if spec.value_type == "enum":
        if not isinstance(value, str):
            raise ValueError(f"setting {field_name} must be an enum string")
        if spec.allowed_values and value not in spec.allowed_values:
            raise ValueError(f"setting {field_name} is not an allowed enum value")
        return value
    if spec.value_type == "string_list":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"setting {field_name} must be a string list")
        return value
    if spec.value_type == "json":
        return value
    raise ValueError(f"setting value_type is invalid: {spec.value_type!r}")


def _validate_numeric_bounds(spec: SettingSpec, value: float, field_name: str) -> None:
    if spec.min_value is not None and value < spec.min_value:
        raise ValueError(f"setting {field_name} is below min_value")
    if spec.max_value is not None and value > spec.max_value:
        raise ValueError(f"setting {field_name} is above max_value")
