from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.base import create_router
from core.context import get_current_context
from core.db import unit_of_work
from core.exceptions import AppError
from core.serialization import Envelope, ListEnvelope, Pagination, ok, ok_list
from platform_apps.audit.models import AuditExportRecord, AuditLog
from platform_apps.audit.schemas import (
    AuditChainVerifyRead,
    AuditChainVerifyRequest,
    AuditExportCreateRequest,
    AuditExportRead,
    AuditLogRead,
    AuditRetentionRead,
    AuditRetentionRequest,
)
from platform_apps.audit.services import (
    AuditExportDestination,
    AuditExportService,
    AuditRetentionService,
    AuditService,
    LocalSiemAuditExportSink,
    LocalWormAuditExportSink,
)

log_router = create_router(
    "/platform/audit/logs",
    tags=["platform-audit"],
    tenant_required=False,
    permissions=["audit_log:read"],
    permission_scope="platform",
)
verify_router = create_router(
    "/platform/audit/verify",
    tags=["platform-audit"],
    tenant_required=False,
    permissions=["audit_log:read"],
    permission_scope="platform",
)
export_read_router = create_router(
    "/platform/audit/exports",
    tags=["platform-audit"],
    tenant_required=False,
    permissions=["audit_log:read"],
    permission_scope="platform",
)
export_router = create_router(
    "/platform/audit/exports",
    tags=["platform-audit"],
    tenant_required=False,
    permissions=["audit_log:export"],
    permission_scope="platform",
    tenant_operation="write",
)
retention_router = create_router(
    "/platform/audit/retention",
    tags=["platform-audit"],
    tenant_required=False,
    permissions=["audit_log:export"],
    permission_scope="platform",
    tenant_operation="write",
)

router = log_router


@log_router.get("", response_model=ListEnvelope[AuditLogRead])
async def list_audit_logs(
    request: Request,
    tenant_id: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    result: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, object]:
    if page < 1 or page_size < 1:
        raise AppError("VALIDATION_ERROR", "page and page_size must be positive", status_code=400)
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        statement = _audit_log_statement(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            result=result,
            request_id=request_id,
            trace_id=trace_id,
            created_from=created_from,
            created_to=created_to,
        )
        total = int(
            await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = list(
            (
                await session.execute(
                    statement.order_by(
                        AuditLog.created_at.asc(),
                        AuditLog.resource_id.asc(),
                        AuditLog.id.asc(),
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        return ok_list(
            [_audit_log_read(row) for row in rows],
            Pagination(
                total=total,
                page=page,
                page_size=page_size,
                has_next=page * page_size < total,
            ),
        )


@verify_router.post("", response_model=Envelope[AuditChainVerifyRead])
async def verify_audit_hash_chain(
    request: Request,
    payload: AuditChainVerifyRequest,
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        verification = await AuditService(session).verify_hash_chain(payload.tenant_id)
        return ok(
            {
                "tenant_id": verification.tenant_id,
                "checked": verification.checked,
                "valid": verification.valid,
                "errors": list(verification.errors),
            }
        )


@export_read_router.get("", response_model=ListEnvelope[AuditExportRead])
async def list_audit_exports(
    request: Request,
    tenant_id: str | None = None,
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        statement = select(AuditExportRecord)
        if tenant_id is None:
            statement = statement.where(AuditExportRecord.tenant_id.is_(None))
        else:
            statement = statement.where(AuditExportRecord.tenant_id == tenant_id)
        rows = list(
            (
                await session.execute(
                    statement.order_by(
                        AuditExportRecord.created_at.asc(),
                        AuditExportRecord.id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        return ok_list(
            [_audit_export_read(row) for row in rows],
            Pagination(
                total=len(rows),
                page=1,
                page_size=max(len(rows), 1),
                has_next=False,
            ),
        )


@export_router.post("", response_model=Envelope[AuditExportRead])
async def create_audit_export(
    request: Request,
    payload: AuditExportCreateRequest,
) -> dict[str, object]:
    context = _request_context()
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        record = await AuditExportService(session).export_logs(
            tenant_id=payload.tenant_id,
            destination_type=_audit_export_destination(payload.destination_type),
            sink=_audit_export_sink(
                payload.destination_type,
                _audit_export_root(request, payload.destination_root),
            ),
            export_id=payload.export_id,
            actor_id=context.user_id,
            request_id=context.request_id,
        )
        return ok(_audit_export_read(record))


@retention_router.post("", response_model=Envelope[AuditRetentionRead])
async def apply_audit_retention_policy(
    request: Request,
    payload: AuditRetentionRequest,
) -> dict[str, object]:
    async with unit_of_work(_session_factory(request)) as uow:
        session = _active_session(uow.session)
        result = await AuditRetentionService(session).apply_policy(
            tenant_id=payload.tenant_id,
            older_than=payload.older_than,
            dry_run=payload.dry_run,
        )
        return ok(
            {
                "tenant_id": result.tenant_id,
                "older_than": result.older_than,
                "matched_count": result.matched_count,
                "deleted_count": result.deleted_count,
                "dry_run": result.dry_run,
                "chain_safe": result.chain_safe,
                "oldest_created_at": result.oldest_created_at,
                "newest_created_at": result.newest_created_at,
            }
        )


def _session_factory(request: Request):
    return request.app.state.session_factory


def _active_session(session: AsyncSession | None) -> AsyncSession:
    if session is None:
        raise AppError("SYSTEM_ERROR", "Database session is not available", status_code=500)
    return session


def _request_context():
    context = get_current_context()
    if context is None or not context.user_id:
        raise AppError("AUTH_INVALID_TOKEN", "Authenticated user is required", status_code=401)
    return context


def _audit_log_statement(
    *,
    tenant_id: str | None,
    actor_id: str | None,
    action: str | None,
    resource_type: str | None,
    result: str | None,
    request_id: str | None,
    trace_id: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
):
    if created_from is not None and created_to is not None and created_from > created_to:
        raise AppError(
            "VALIDATION_ERROR",
            "created_from must be before created_to",
            status_code=400,
        )
    statement = select(AuditLog)
    if tenant_id is None:
        statement = statement.where(AuditLog.tenant_id.is_(None))
    else:
        statement = statement.where(AuditLog.tenant_id == tenant_id)
    if actor_id:
        statement = statement.where(AuditLog.actor_id == actor_id)
    if action:
        statement = statement.where(AuditLog.action == action)
    if resource_type:
        statement = statement.where(AuditLog.resource_type == resource_type)
    if result:
        statement = statement.where(AuditLog.result == result)
    if request_id:
        statement = statement.where(AuditLog.request_id == request_id)
    if trace_id:
        statement = statement.where(AuditLog.trace_id == trace_id)
    if created_from is not None:
        statement = statement.where(AuditLog.created_at >= _datetime_storage(created_from))
    if created_to is not None:
        statement = statement.where(AuditLog.created_at <= _datetime_storage(created_to))
    return statement


def _audit_export_destination(value: str) -> AuditExportDestination:
    if value not in {"worm", "siem"}:
        raise AppError(
            "VALIDATION_ERROR",
            "audit export destination_type is invalid",
            status_code=400,
        )
    return value  # type: ignore[return-value]


def _audit_export_root(request: Request, explicit: str | None) -> Path:
    if explicit is not None and explicit.strip():
        return Path(explicit)
    configured = getattr(request.app.state, "audit_export_root", None)
    if configured is not None:
        return Path(configured)
    return Path("audit-exports")


def _audit_export_sink(destination_type: str, root: Path):
    destination = _audit_export_destination(destination_type)
    if destination == "siem":
        return LocalSiemAuditExportSink(root)
    return LocalWormAuditExportSink(root)


def _datetime_storage(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _audit_log_read(audit_log: AuditLog) -> dict[str, Any]:
    return {
        "id": audit_log.id,
        "tenant_id": audit_log.tenant_id,
        "actor_id": audit_log.actor_id,
        "action": audit_log.action,
        "resource_type": audit_log.resource_type,
        "resource_id": audit_log.resource_id,
        "result": audit_log.result,
        "reason": audit_log.reason,
        "policy_version": audit_log.policy_version,
        "request_id": audit_log.request_id,
        "trace_id": audit_log.trace_id,
        "route": audit_log.route,
        "method": audit_log.method,
        "payload": audit_log.payload,
        "hash_prev": audit_log.hash_prev,
        "hash": audit_log.hash,
        "created_at": _datetime_read(audit_log.created_at),
    }


def _audit_export_read(record: AuditExportRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "actor_id": record.actor_id,
        "destination_type": record.destination_type,
        "destination_uri": record.destination_uri,
        "status": record.status,
        "request_id": record.request_id,
        "filters": record.filters,
        "record_count": record.record_count,
        "hash_root": record.hash_root,
        "hash_tip": record.hash_tip,
        "checksum_sha256": record.checksum_sha256,
        "error_message": record.error_message,
        "exported_at": _datetime_read(record.exported_at),
        "created_at": _datetime_read(record.created_at),
    }


def _datetime_read(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
