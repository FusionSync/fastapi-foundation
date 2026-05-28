from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppError
from core.permissions.models import ProjectedPolicy


class AuditRecorder(Protocol):
    async def record(
        self,
        *,
        action: str,
        resource_type: str,
        result: str,
        tenant_id: str | None = None,
        actor_id: str | None = None,
        resource_id: str | None = None,
        reason: str | None = None,
        policy_version: int | None = None,
        request_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> object:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    tenant_id: str
    user_id: str
    resource: str
    action: str
    reason: str
    policy_version: int | None = None


class AuthorizationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.session = session
        self.audit = audit

    async def authorize(
        self,
        *,
        user_id: str,
        tenant_id: str,
        resource: str,
        action: str,
        resource_id: str | None = None,
        request_id: str | None = None,
    ) -> AuthorizationDecision:
        self._validate_authorization_input(
            user_id=user_id,
            tenant_id=tenant_id,
            resource=resource,
            action=action,
        )
        subject = f"user:{user_id}"
        policy = await self._matching_policy(
            tenant_id=tenant_id,
            subject=subject,
            resource=resource,
            action=action,
        )
        if policy is not None:
            return AuthorizationDecision(
                allowed=True,
                tenant_id=tenant_id,
                user_id=user_id,
                resource=resource,
                action=action,
                reason="matched_projected_policy",
                policy_version=policy.policy_version,
            )

        reason = "missing_projected_policy"
        await self._record_denial(
            tenant_id=tenant_id,
            user_id=user_id,
            resource=resource,
            action=action,
            resource_id=resource_id,
            request_id=request_id,
            reason=reason,
            subject=subject,
        )
        return AuthorizationDecision(
            allowed=False,
            tenant_id=tenant_id,
            user_id=user_id,
            resource=resource,
            action=action,
            reason=reason,
        )

    async def require(
        self,
        *,
        user_id: str,
        tenant_id: str,
        resource: str,
        action: str,
        resource_id: str | None = None,
        request_id: str | None = None,
    ) -> AuthorizationDecision:
        decision = await self.authorize(
            user_id=user_id,
            tenant_id=tenant_id,
            resource=resource,
            action=action,
            resource_id=resource_id,
            request_id=request_id,
        )
        if decision.allowed:
            return decision
        raise AppError(
            "PERMISSION_DENIED",
            "Permission denied",
            status_code=403,
            details={
                "tenant_id": tenant_id,
                "user_id": user_id,
                "resource": resource,
                "action": action,
            },
        )

    async def _matching_policy(
        self,
        *,
        tenant_id: str,
        subject: str,
        resource: str,
        action: str,
    ) -> ProjectedPolicy | None:
        result = await self.session.execute(
            select(ProjectedPolicy)
            .where(ProjectedPolicy.tenant_id == tenant_id)
            .where(ProjectedPolicy.subject == subject)
            .where(ProjectedPolicy.resource == resource)
            .where(ProjectedPolicy.action == action)
            .where(ProjectedPolicy.effect == "allow")
            .order_by(ProjectedPolicy.policy_version.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def _record_denial(
        self,
        *,
        tenant_id: str,
        user_id: str,
        resource: str,
        action: str,
        resource_id: str | None,
        request_id: str | None,
        reason: str,
        subject: str,
    ) -> None:
        if self.audit is None:
            return
        await self.audit.record(
            action="authorization.denied",
            resource_type=resource,
            resource_id=resource_id,
            result="denied",
            tenant_id=tenant_id,
            actor_id=user_id,
            reason=reason,
            request_id=request_id,
            payload={
                "resource": resource,
                "action": action,
                "subject": subject,
                "reason": reason,
            },
        )

    def _validate_authorization_input(
        self,
        *,
        user_id: str,
        tenant_id: str,
        resource: str,
        action: str,
    ) -> None:
        missing = [
            name
            for name, value in {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "resource": resource,
                "action": action,
            }.items()
            if not value.strip()
        ]
        if missing:
            raise AppError(
                "VALIDATION_ERROR",
                f"authorization missing required fields: {missing}",
                status_code=400,
            )
