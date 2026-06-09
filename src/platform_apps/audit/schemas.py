from __future__ import annotations

from datetime import datetime
from typing import Any

from core.base import CreateSchema, Schema


class AuditLogRead(Schema):
    id: str
    tenant_id: str | None = None
    actor_id: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    result: str
    reason: str | None = None
    policy_version: int | None = None
    request_id: str | None = None
    trace_id: str | None = None
    route: str | None = None
    method: str | None = None
    payload: dict[str, Any]
    hash_prev: str | None = None
    hash: str
    created_at: datetime | None = None


class AuditChainVerifyRequest(CreateSchema):
    tenant_id: str | None = None


class AuditChainVerifyRead(Schema):
    tenant_id: str | None = None
    checked: int
    valid: bool
    errors: list[str]


class AuditExportCreateRequest(CreateSchema):
    tenant_id: str | None = None
    destination_type: str = "worm"
    export_id: str | None = None
    destination_root: str | None = None


class AuditExportRead(Schema):
    id: str
    tenant_id: str | None = None
    actor_id: str | None = None
    destination_type: str
    destination_uri: str | None = None
    status: str
    request_id: str | None = None
    filters: dict[str, Any]
    record_count: int
    hash_root: str | None = None
    hash_tip: str | None = None
    checksum_sha256: str | None = None
    error_message: str | None = None
    exported_at: datetime | None = None
    created_at: datetime | None = None


class AuditRetentionRequest(CreateSchema):
    tenant_id: str | None = None
    older_than: datetime
    dry_run: bool = True


class AuditRetentionRead(Schema):
    tenant_id: str | None = None
    older_than: datetime
    matched_count: int
    deleted_count: int
    dry_run: bool
    chain_safe: bool
    oldest_created_at: datetime | None = None
    newest_created_at: datetime | None = None
