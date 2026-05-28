from __future__ import annotations

from dataclasses import dataclass

from core.audit import AuditRecorder
from core.cache import CacheProvider
from core.exceptions import AppError
from core.rate_limit.rules import RateLimitIdentity, RateLimitRule


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    rule_name: str
    key: str
    limit: int
    current: int
    remaining: int
    retry_after: int
    reason: str
    fail_open: bool = False

    @property
    def headers(self) -> dict[str, str]:
        if self.allowed:
            return {}
        return {"Retry-After": str(self.retry_after)}

    def details(self) -> dict[str, object]:
        details: dict[str, object] = {
            "rule": self.rule_name,
            "limit": self.limit,
            "current": self.current,
            "remaining": self.remaining,
            "retry_after": self.retry_after,
            "key": self.key,
        }
        if self.reason != "limit_exceeded":
            details["reason"] = self.reason
        return details


class CacheRateLimiter:
    def __init__(
        self,
        cache: CacheProvider,
        *,
        audit: AuditRecorder | None = None,
    ) -> None:
        self.cache = cache
        self.audit = audit

    async def check(
        self,
        rule: RateLimitRule,
        identity: RateLimitIdentity,
        *,
        amount: int = 1,
    ) -> RateLimitDecision:
        if amount <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "Rate limit amount must be greater than zero",
                status_code=400,
            )
        key = rule.key_for(identity)
        try:
            current = await self.cache.incr(key, amount=amount, ttl_seconds=rule.window_seconds)
        except Exception:
            return await self._cache_failure_decision(rule, identity, key)

        allowed = current <= rule.limit
        decision = RateLimitDecision(
            allowed=allowed,
            rule_name=rule.name,
            key=key,
            limit=rule.limit,
            current=current,
            remaining=max(rule.limit - current, 0),
            retry_after=0 if allowed else rule.window_seconds,
            reason="allowed" if allowed else "limit_exceeded",
        )
        if not allowed:
            await self._record_hit(decision, identity)
        return decision

    async def require(
        self,
        rule: RateLimitRule,
        identity: RateLimitIdentity,
        *,
        amount: int = 1,
    ) -> RateLimitDecision:
        decision = await self.check(rule, identity, amount=amount)
        if decision.allowed:
            return decision
        raise AppError(
            "RATE_LIMITED",
            "Rate limit exceeded",
            status_code=429,
            details=decision.details(),
            headers=decision.headers,
        )

    async def _cache_failure_decision(
        self,
        rule: RateLimitRule,
        identity: RateLimitIdentity,
        key: str,
    ) -> RateLimitDecision:
        decision = RateLimitDecision(
            allowed=not rule.fail_closed,
            rule_name=rule.name,
            key=key,
            limit=rule.limit,
            current=0,
            remaining=rule.limit if not rule.fail_closed else 0,
            retry_after=0 if not rule.fail_closed else rule.window_seconds,
            reason="cache_unavailable",
            fail_open=not rule.fail_closed,
        )
        if not decision.allowed:
            await self._record_hit(decision, identity)
        return decision

    async def _record_hit(
        self,
        decision: RateLimitDecision,
        identity: RateLimitIdentity,
    ) -> None:
        if self.audit is None:
            return
        await self.audit.record(
            action="rate_limit.hit",
            resource_type="route",
            resource_id=identity.route,
            result="denied",
            tenant_id=identity.tenant_id,
            actor_id=identity.user_id,
            reason=decision.reason,
            payload={
                "rule": decision.rule_name,
                "key": decision.key,
                "limit": decision.limit,
                "current": decision.current,
                "retry_after": decision.retry_after,
            },
        )
