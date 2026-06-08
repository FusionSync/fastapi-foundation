from __future__ import annotations

from typing import Any

from core.base import BaseSchema, CreateSchema


class SettingDefinitionRead(BaseSchema):
    app_label: str
    module: str
    key: str
    value_type: str
    default: Any
    scopes: list[str]
    category: str
    description: str
    required: bool
    runtime_mutable: bool
    sensitive: bool
    secret_ref_only: bool
    risk_level: str
    cache_ttl_seconds: int | None
    allowed_values: list[str]
    kind: str
    deprecated: bool


class SettingValueUpsertRequest(CreateSchema):
    value: Any | None = None
    secret_ref: str | None = None
    reason: str | None = None


class SettingValueRead(BaseSchema):
    id: str
    module: str
    key: str
    scope: str
    scope_id: str
    value: Any | None
    secret_ref: str | None
    value_type: str
    version: int
    status: str
    updated_by: str
    reason: str | None


class ResolvedSettingRead(BaseSchema):
    module: str
    key: str
    scope: str
    scope_id: str
    source: str
    value: Any | None
    version: int
