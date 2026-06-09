from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import get_current_context
from core.exceptions import AppError
from core.locks import LockProvider
from core.security import redact_sensitive_data
from platform_apps.audit.models import AuditExportRecord, AuditLog

AuditResult = Literal["success", "failure", "denied"]
AuditExportDestination = Literal["worm", "siem"]
_AUDIT_CHAIN_LOCKS: dict[str, asyncio.Lock] = {}
_AUDIT_CHAIN_LOCKS_GUARD = asyncio.Lock()
_SESSION_AUDIT_CHAIN_LOCKS = "audit_chain_locks"
_SESSION_AUDIT_CHAIN_RELEASE_REGISTERED = "audit_chain_release_registered"


@dataclass(frozen=True, slots=True)
class AuditChainVerificationResult:
    tenant_id: str | None
    checked: int
    valid: bool
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditExportSinkResult:
    destination_uri: str


class AuditExportSink(Protocol):
    async def write(self, *, export_id: str, payload: bytes) -> AuditExportSinkResult: ...


class LocalWormAuditExportSink:
    def __init__(self, root: str | Path, *, suffix: str = ".jsonl") -> None:
        self.root = Path(root)
        self.suffix = suffix

    async def write(self, *, export_id: str, payload: bytes) -> AuditExportSinkResult:
        file_name = _safe_export_file_name(export_id, self.suffix)
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / file_name
        try:
            with destination.open("xb") as export_file:
                export_file.write(payload)
        except FileExistsError as exc:
            raise AppError(
                "CONFLICT",
                "audit export object already exists",
                status_code=409,
                details={"destination_uri": str(destination)},
            ) from exc
        return AuditExportSinkResult(destination_uri=str(destination))


class LocalSiemAuditExportSink(LocalWormAuditExportSink):
    def __init__(self, root: str | Path) -> None:
        super().__init__(root, suffix=".siem.jsonl")


@dataclass(frozen=True, slots=True)
class _DistributedAuditChainLock:
    provider: LockProvider
    lock_key: str
    owner_token: str


@dataclass(frozen=True, slots=True)
class _HeldAuditChainLock:
    local_lock: asyncio.Lock
    distributed_lock: _DistributedAuditChainLock | None = None


class AuditExportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def export_logs(
        self,
        *,
        tenant_id: str | None,
        destination_type: AuditExportDestination,
        sink: AuditExportSink,
        export_id: str | None = None,
        actor_id: str | None = None,
        request_id: str | None = None,
    ) -> AuditExportRecord:
        resolved_export_id = export_id or _new_export_id()
        _validate_export_request(
            export_id=resolved_export_id,
            destination_type=destination_type,
        )

        audit_logs = await self._audit_logs(tenant_id)
        verification_errors = _verify_hash_chain(audit_logs)
        if verification_errors:
            raise AppError(
                "CONFLICT",
                "audit hash chain is invalid",
                status_code=409,
                details={
                    "tenant_id": tenant_id,
                    "errors": list(verification_errors),
                },
            )

        ordered_logs = _order_valid_hash_chain(audit_logs)
        hash_root = ordered_logs[0].hash if ordered_logs else None
        hash_tip = ordered_logs[-1].hash if ordered_logs else None
        filters = _audit_export_filters(tenant_id)
        payload = _audit_export_payload(
            export_id=resolved_export_id,
            destination_type=destination_type,
            tenant_id=tenant_id,
            filters=filters,
            record_count=len(ordered_logs),
            hash_root=hash_root,
            hash_tip=hash_tip,
            audit_logs=ordered_logs,
        )
        checksum = hashlib.sha256(payload).hexdigest()

        context = get_current_context()
        export_record = AuditExportRecord(
            id=resolved_export_id,
            tenant_id=tenant_id,
            actor_id=actor_id or (context.user_id if context else None),
            destination_type=destination_type,
            status="pending",
            request_id=request_id or (context.request_id if context else None),
            filters=filters,
            record_count=len(ordered_logs),
            hash_root=hash_root,
            hash_tip=hash_tip,
            checksum_sha256=checksum,
        )
        self.session.add(export_record)
        await self.session.flush()

        try:
            sink_result = await sink.write(export_id=resolved_export_id, payload=payload)
        except AppError as exc:
            export_record.status = "failed"
            export_record.error_message = exc.message
            await self.session.flush()
            raise

        export_record.status = "succeeded"
        export_record.destination_uri = sink_result.destination_uri
        export_record.exported_at = datetime.now(UTC)
        await self.session.flush()
        return export_record

    async def _audit_logs(self, tenant_id: str | None) -> list[AuditLog]:
        statement = select(AuditLog)
        if tenant_id is None:
            statement = statement.where(AuditLog.tenant_id.is_(None))
        else:
            statement = statement.where(AuditLog.tenant_id == tenant_id)
        result = await self.session.execute(statement.order_by(AuditLog.id))
        return list(result.scalars().all())


@dataclass(frozen=True, slots=True)
class AuditRetentionResult:
    tenant_id: str | None
    older_than: datetime
    matched_count: int
    deleted_count: int
    dry_run: bool
    chain_safe: bool
    oldest_created_at: datetime | None
    newest_created_at: datetime | None


class AuditRetentionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def apply_policy(
        self,
        *,
        tenant_id: str | None,
        older_than: datetime,
        dry_run: bool = True,
    ) -> AuditRetentionResult:
        cutoff = _datetime_utc(older_than)
        audit_logs = await self._audit_logs(tenant_id)
        expired = [
            audit_log
            for audit_log in audit_logs
            if audit_log.created_at is not None and _datetime_utc(audit_log.created_at) < cutoff
        ]
        chain_safe = not expired or len(expired) == len(audit_logs)
        if not dry_run and expired and not chain_safe:
            raise AppError(
                "CONFLICT",
                "audit retention would break the hash chain; export a complete chain first",
                status_code=409,
                details={
                    "tenant_id": tenant_id,
                    "matched_count": len(expired),
                    "total_count": len(audit_logs),
                },
            )

        deleted_count = 0
        if not dry_run and expired:
            await self.session.execute(
                delete(AuditLog).where(AuditLog.id.in_([audit_log.id for audit_log in expired]))
            )
            deleted_count = len(expired)
            await self.session.flush()

        created_values = [
            _datetime_utc(audit_log.created_at)
            for audit_log in expired
            if audit_log.created_at is not None
        ]
        return AuditRetentionResult(
            tenant_id=tenant_id,
            older_than=cutoff,
            matched_count=len(expired),
            deleted_count=deleted_count,
            dry_run=dry_run,
            chain_safe=chain_safe,
            oldest_created_at=min(created_values) if created_values else None,
            newest_created_at=max(created_values) if created_values else None,
        )

    async def _audit_logs(self, tenant_id: str | None) -> list[AuditLog]:
        statement = select(AuditLog)
        if tenant_id is None:
            statement = statement.where(AuditLog.tenant_id.is_(None))
        else:
            statement = statement.where(AuditLog.tenant_id == tenant_id)
        result = await self.session.execute(
            statement.order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        )
        return list(result.scalars().all())


class AuditService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        lock_provider: LockProvider | None = None,
        lock_owner_token: str | None = None,
        lock_ttl_seconds: int = 300,
    ) -> None:
        self.session = session
        self.lock_provider = lock_provider
        self.lock_owner_token = lock_owner_token
        self.lock_ttl_seconds = lock_ttl_seconds

    async def record(
        self,
        *,
        action: str,
        resource_type: str,
        result: AuditResult,
        tenant_id: str | None = None,
        actor_id: str | None = None,
        actor_type: str = "user",
        auth_provider: str | None = None,
        session_id: str | None = None,
        resource_id: str | None = None,
        reason: str | None = None,
        policy_version: int | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        route: str | None = None,
        method: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditLog:
        self._validate_required(action=action, resource_type=resource_type, result=result)
        context = get_current_context()
        resolved_tenant_id = tenant_id or (context.tenant_id if context else None)
        await _acquire_audit_chain_lock(
            self.session,
            resolved_tenant_id,
            lock_provider=self.lock_provider,
            lock_owner_token=self.lock_owner_token,
            lock_ttl_seconds=self.lock_ttl_seconds,
        )
        previous_hash = await self._latest_hash(resolved_tenant_id)
        redacted_payload = redact_sensitive_data(payload or {})
        audit_log = AuditLog(
            tenant_id=resolved_tenant_id,
            actor_id=actor_id or (context.user_id if context else None),
            actor_type=actor_type,
            auth_provider=auth_provider,
            session_id=session_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            reason=reason,
            policy_version=policy_version,
            request_id=request_id or (context.request_id if context else None),
            trace_id=trace_id or (context.trace_id if context else None),
            route=route or (context.route if context else None),
            method=method or (context.method if context else None),
            ip_address=ip_address or (context.ip_address if context else None),
            user_agent=user_agent or (context.user_agent if context else None),
            payload=redacted_payload,
            hash_prev=previous_hash,
            hash="",
        )
        audit_log.hash = audit_hash(audit_log)
        self.session.add(audit_log)
        await self.session.flush()
        return audit_log

    async def _latest_hash(self, tenant_id: str | None) -> str | None:
        statement = select(AuditLog.hash)
        if tenant_id is None:
            statement = statement.where(AuditLog.tenant_id.is_(None))
        else:
            statement = statement.where(AuditLog.tenant_id == tenant_id)
        result = await self.session.execute(
            statement.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def verify_hash_chain(self, tenant_id: str | None) -> AuditChainVerificationResult:
        statement = select(AuditLog)
        if tenant_id is None:
            statement = statement.where(AuditLog.tenant_id.is_(None))
        else:
            statement = statement.where(AuditLog.tenant_id == tenant_id)
        result = await self.session.execute(statement)
        audit_logs = list(result.scalars().all())
        errors = _verify_hash_chain(audit_logs)
        return AuditChainVerificationResult(
            tenant_id=tenant_id,
            checked=len(audit_logs),
            valid=not errors,
            errors=tuple(errors),
        )

    def _validate_required(
        self,
        *,
        action: str,
        resource_type: str,
        result: str,
    ) -> None:
        if not action.strip():
            raise AppError("VALIDATION_ERROR", "audit action is required", status_code=400)
        if not resource_type.strip():
            raise AppError("VALIDATION_ERROR", "audit resource_type is required", status_code=400)
        if result not in {"success", "failure", "denied"}:
            raise AppError("VALIDATION_ERROR", "audit result is invalid", status_code=400)


async def _acquire_audit_chain_lock(
    session: AsyncSession,
    tenant_id: str | None,
    *,
    lock_provider: LockProvider | None = None,
    lock_owner_token: str | None = None,
    lock_ttl_seconds: int = 300,
) -> None:
    key = _audit_chain_lock_key(tenant_id)
    held_locks = session.sync_session.info.setdefault(_SESSION_AUDIT_CHAIN_LOCKS, {})
    if key in held_locks:
        return

    async with _AUDIT_CHAIN_LOCKS_GUARD:
        lock = _AUDIT_CHAIN_LOCKS.setdefault(key, asyncio.Lock())
    await lock.acquire()
    try:
        distributed_lock = await _acquire_distributed_audit_chain_lock(
            session,
            tenant_id,
            lock_provider=lock_provider,
            lock_owner_token=lock_owner_token,
            lock_ttl_seconds=lock_ttl_seconds,
        )
        held_locks[key] = _HeldAuditChainLock(
            local_lock=lock,
            distributed_lock=distributed_lock,
        )
        _register_audit_chain_lock_release(session)
    except BaseException:
        if lock.locked():
            lock.release()
        raise


async def _acquire_distributed_audit_chain_lock(
    session: AsyncSession,
    tenant_id: str | None,
    *,
    lock_provider: LockProvider | None,
    lock_owner_token: str | None,
    lock_ttl_seconds: int,
) -> _DistributedAuditChainLock | None:
    if lock_provider is None:
        return None
    lock_key = _audit_chain_distributed_lock_key(tenant_id)
    owner_token = lock_owner_token or _audit_chain_owner_token(session, tenant_id)
    await lock_provider.require_acquire(
        lock_key,
        owner_token=owner_token,
        ttl_seconds=lock_ttl_seconds,
    )
    return _DistributedAuditChainLock(
        provider=lock_provider,
        lock_key=lock_key,
        owner_token=owner_token,
    )


def _register_audit_chain_lock_release(session: AsyncSession) -> None:
    sync_session = session.sync_session
    if sync_session.info.get(_SESSION_AUDIT_CHAIN_RELEASE_REGISTERED):
        return

    def release_locks_after_transaction_end(session_, transaction) -> None:
        if transaction.parent is not None:
            return
        held_locks = session_.info.pop(_SESSION_AUDIT_CHAIN_LOCKS, {})
        for held_lock in held_locks.values():
            if held_lock.local_lock.locked():
                held_lock.local_lock.release()
            if held_lock.distributed_lock is not None:
                _schedule_distributed_audit_chain_lock_release(held_lock.distributed_lock)

    sync_session.info[_SESSION_AUDIT_CHAIN_RELEASE_REGISTERED] = True
    event.listen(sync_session, "after_transaction_end", release_locks_after_transaction_end)


def _audit_chain_lock_key(tenant_id: str | None) -> str:
    return f"tenant:{tenant_id}" if tenant_id is not None else "platform"


def _audit_chain_distributed_lock_key(tenant_id: str | None) -> str:
    return f"audit:hash-chain:{_audit_chain_lock_key(tenant_id)}"


def _audit_chain_owner_token(session: AsyncSession, tenant_id: str | None) -> str:
    return f"audit-chain:{id(session.sync_session)}:{_audit_chain_lock_key(tenant_id)}"


def _schedule_distributed_audit_chain_lock_release(
    distributed_lock: _DistributedAuditChainLock,
) -> None:
    task = asyncio.create_task(
        distributed_lock.provider.release(
            distributed_lock.lock_key,
            owner_token=distributed_lock.owner_token,
        )
    )
    task.add_done_callback(_consume_lock_release_result)


def _consume_lock_release_result(task: asyncio.Task[bool]) -> None:
    try:
        task.result()
    except Exception:
        return


def _new_export_id() -> str:
    from uuid import uuid4

    return str(uuid4())


def _validate_export_request(
    *,
    export_id: str,
    destination_type: str,
) -> None:
    if not export_id.strip():
        raise AppError("VALIDATION_ERROR", "audit export_id is required", status_code=400)
    if destination_type not in {"worm", "siem"}:
        raise AppError(
            "VALIDATION_ERROR",
            "audit export destination_type is invalid",
            status_code=400,
        )


def _safe_export_file_name(export_id: str, suffix: str) -> str:
    _validate_export_request(export_id=export_id, destination_type="worm")
    if not suffix.startswith("."):
        raise AppError("VALIDATION_ERROR", "audit export suffix is invalid", status_code=400)
    if export_id in {".", ".."} or any(separator in export_id for separator in ("/", "\\", ":")):
        raise AppError("VALIDATION_ERROR", "audit export_id is invalid", status_code=400)
    return f"{export_id}{suffix}"


def _datetime_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _audit_export_filters(tenant_id: str | None) -> dict[str, object]:
    return {"tenant_id": tenant_id}


def _audit_export_payload(
    *,
    export_id: str,
    destination_type: AuditExportDestination,
    tenant_id: str | None,
    filters: dict[str, object],
    record_count: int,
    hash_root: str | None,
    hash_tip: str | None,
    audit_logs: Sequence[AuditLog],
) -> bytes:
    lines: list[dict[str, object]] = [
        {
            "type": "audit_export_manifest",
            "format": "audit.ndjson.v1",
            "export_id": export_id,
            "destination_type": destination_type,
            "tenant_id": tenant_id,
            "filters": filters,
            "record_count": record_count,
            "hash_root": hash_root,
            "hash_tip": hash_tip,
        }
    ]
    lines.extend(_audit_log_export_dict(audit_log) for audit_log in audit_logs)
    payload = "".join(
        f"{json.dumps(line, ensure_ascii=True, sort_keys=True, separators=(',', ':'))}\n"
        for line in lines
    )
    return payload.encode("utf-8")


def _audit_log_export_dict(audit_log: AuditLog) -> dict[str, object]:
    return {
        "type": "audit_log",
        "id": audit_log.id,
        "tenant_id": audit_log.tenant_id,
        "actor_id": audit_log.actor_id,
        "actor_type": audit_log.actor_type,
        "auth_provider": audit_log.auth_provider,
        "session_id": audit_log.session_id,
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
        "ip_address": audit_log.ip_address,
        "user_agent": audit_log.user_agent,
        "payload": audit_log.payload,
        "hash_prev": audit_log.hash_prev,
        "hash": audit_log.hash,
        "created_at": audit_log.created_at.isoformat() if audit_log.created_at else None,
    }


def _order_valid_hash_chain(audit_logs: Sequence[AuditLog]) -> list[AuditLog]:
    if not audit_logs:
        return []

    by_prev: dict[str | None, list[AuditLog]] = {}
    for audit_log in audit_logs:
        by_prev.setdefault(audit_log.hash_prev, []).append(audit_log)

    ordered = [by_prev[None][0]]
    while True:
        children = by_prev.get(ordered[-1].hash, [])
        if not children:
            return ordered
        ordered.append(children[0])


def _verify_hash_chain(audit_logs: list[AuditLog]) -> list[str]:
    if not audit_logs:
        return []

    errors: list[str] = []
    by_hash: dict[str, AuditLog] = {}
    children_by_prev: dict[str, list[AuditLog]] = {}
    roots: list[AuditLog] = []

    for audit_log in audit_logs:
        if audit_log.hash != audit_hash(audit_log):
            errors.append(f"hash_mismatch:{audit_log.id}")
        if audit_log.hash in by_hash:
            errors.append(f"duplicate_hash:{audit_log.hash}")
        else:
            by_hash[audit_log.hash] = audit_log

        if audit_log.hash_prev is None:
            roots.append(audit_log)
        else:
            children_by_prev.setdefault(audit_log.hash_prev, []).append(audit_log)

    for audit_log in audit_logs:
        if audit_log.hash_prev is not None and audit_log.hash_prev not in by_hash:
            errors.append(f"missing_prev:{audit_log.id}")

    for prev_hash, children in children_by_prev.items():
        if len(children) > 1:
            errors.append(f"branch:{prev_hash}")

    if len(roots) != 1:
        errors.append(f"invalid_root_count:{len(roots)}")
        return errors

    visited: set[str] = set()
    current = roots[0]
    while current.hash not in visited:
        visited.add(current.hash)
        children = children_by_prev.get(current.hash, [])
        if len(children) != 1:
            break
        current = children[0]

    if len(visited) != len(audit_logs):
        errors.append(f"disconnected_chain:{len(audit_logs) - len(visited)}")

    return errors


def audit_hash(audit_log: AuditLog) -> str:
    payload = {
        "tenant_id": audit_log.tenant_id,
        "actor_id": audit_log.actor_id,
        "actor_type": audit_log.actor_type,
        "action": audit_log.action,
        "resource_type": audit_log.resource_type,
        "resource_id": audit_log.resource_id,
        "result": audit_log.result,
        "reason": audit_log.reason,
        "policy_version": audit_log.policy_version,
        "request_id": audit_log.request_id,
        "payload": audit_log.payload,
        "hash_prev": audit_log.hash_prev,
    }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
