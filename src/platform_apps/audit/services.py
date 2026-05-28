from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import get_current_context
from core.exceptions import AppError
from core.security import redact_sensitive_data
from platform_apps.audit.models import AuditLog

AuditResult = Literal["success", "failure", "denied"]
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


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
        ip_address: str | None = None,
        user_agent: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditLog:
        self._validate_required(action=action, resource_type=resource_type, result=result)
        context = get_current_context()
        resolved_tenant_id = tenant_id or (context.tenant_id if context else None)
        await _acquire_audit_chain_lock(self.session, resolved_tenant_id)
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


async def _acquire_audit_chain_lock(session: AsyncSession, tenant_id: str | None) -> None:
    key = _audit_chain_lock_key(tenant_id)
    held_locks = session.sync_session.info.setdefault(_SESSION_AUDIT_CHAIN_LOCKS, {})
    if key in held_locks:
        return

    async with _AUDIT_CHAIN_LOCKS_GUARD:
        lock = _AUDIT_CHAIN_LOCKS.setdefault(key, asyncio.Lock())
    await lock.acquire()
    held_locks[key] = lock
    _register_audit_chain_lock_release(session)


def _register_audit_chain_lock_release(session: AsyncSession) -> None:
    sync_session = session.sync_session
    if sync_session.info.get(_SESSION_AUDIT_CHAIN_RELEASE_REGISTERED):
        return

    def release_locks_after_transaction_end(session_, transaction) -> None:
        if transaction.parent is not None:
            return
        held_locks = session_.info.pop(_SESSION_AUDIT_CHAIN_LOCKS, {})
        for lock in held_locks.values():
            if lock.locked():
                lock.release()

    sync_session.info[_SESSION_AUDIT_CHAIN_RELEASE_REGISTERED] = True
    event.listen(sync_session, "after_transaction_end", release_locks_after_transaction_end)


def _audit_chain_lock_key(tenant_id: str | None) -> str:
    return f"tenant:{tenant_id}" if tenant_id is not None else "platform"


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
