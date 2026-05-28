from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import get_current_context
from core.exceptions import AppError
from core.security import redact_sensitive_data
from platform_apps.audit.models import AuditLog

AuditResult = Literal["success", "failure", "denied"]


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
