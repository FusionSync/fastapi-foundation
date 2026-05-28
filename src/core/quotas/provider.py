from __future__ import annotations

from dataclasses import dataclass

from core.audit import AuditRecorder
from core.exceptions import AppError
from core.quotas.rules import QuotaRule, QuotaSubject
from core.quotas.usage import QuotaUsageStore


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    allowed: bool
    metric: str
    scope: str
    key: str
    limit: int
    current: int
    requested: int
    projected: int
    remaining: int
    reason: str

    def details(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "scope": self.scope,
            "limit": self.limit,
            "current": self.current,
            "requested": self.requested,
            "projected": self.projected,
            "remaining": self.remaining,
            "key": self.key,
        }


class QuotaService:
    def __init__(
        self,
        usage_store: QuotaUsageStore,
        *,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.usage_store = usage_store
        self.audit = audit

    async def check(
        self,
        rule: QuotaRule,
        subject: QuotaSubject,
        *,
        amount: int = 1,
    ) -> QuotaDecision:
        self._validate_amount(amount)
        key = rule.key_for(subject)
        current = await self.usage_store.get_usage(key)
        return self._decision(
            rule=rule,
            key=key,
            current=current,
            amount=amount,
        )

    async def reserve(
        self,
        rule: QuotaRule,
        subject: QuotaSubject,
        *,
        amount: int = 1,
    ) -> QuotaDecision:
        self._validate_amount(amount)
        key = rule.key_for(subject)
        projected = await self.usage_store.reserve(key, amount=amount, limit=rule.limit)
        if projected is not None:
            return self._decision(
                rule=rule,
                key=key,
                current=projected - amount,
                amount=amount,
            )

        current = await self.usage_store.get_usage(key)
        decision = self._decision(rule=rule, key=key, current=current, amount=amount)
        await self._record_exceeded(decision, subject)
        return decision

    async def require_reserve(
        self,
        rule: QuotaRule,
        subject: QuotaSubject,
        *,
        amount: int = 1,
    ) -> QuotaDecision:
        decision = await self.reserve(rule, subject, amount=amount)
        if decision.allowed:
            return decision
        raise AppError(
            "QUOTA_EXCEEDED",
            "Quota exceeded",
            status_code=403,
            details=decision.details(),
        )

    async def release(
        self,
        rule: QuotaRule,
        subject: QuotaSubject,
        *,
        amount: int = 1,
    ) -> int:
        self._validate_amount(amount)
        return await self.usage_store.release(rule.key_for(subject), amount=amount)

    def _decision(
        self,
        *,
        rule: QuotaRule,
        key: str,
        current: int,
        amount: int,
    ) -> QuotaDecision:
        projected = current + amount
        allowed = projected <= rule.limit
        return QuotaDecision(
            allowed=allowed,
            metric=rule.metric,
            scope=rule.scope,
            key=key,
            limit=rule.limit,
            current=current,
            requested=amount,
            projected=projected,
            remaining=max(rule.limit - projected, 0),
            reason="within_limit" if allowed else "quota_exceeded",
        )

    async def _record_exceeded(
        self,
        decision: QuotaDecision,
        subject: QuotaSubject,
    ) -> None:
        if self.audit is None:
            return
        await self.audit.record(
            action="quota.exceeded",
            resource_type="quota",
            resource_id=decision.metric,
            result="denied",
            tenant_id=subject.tenant_id,
            actor_id=subject.user_id,
            reason="quota_exceeded",
            payload={
                "metric": decision.metric,
                "scope": decision.scope,
                "key": decision.key,
                "limit": decision.limit,
                "current": decision.current,
                "requested": decision.requested,
                "projected": decision.projected,
            },
        )

    def _validate_amount(self, amount: int) -> None:
        if amount <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "Quota amount must be greater than zero",
                status_code=400,
            )
