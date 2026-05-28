from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.cache import MemoryCacheProvider
from core.exceptions import AppError
from core.rate_limit import (
    CacheRateLimiter,
    RateLimitIdentity,
    RateLimitRegistry,
    RateLimitRule,
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 5, 28, tzinfo=UTC)

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class FailingCache:
    async def incr(self, *args: Any, **kwargs: Any) -> int:
        raise RuntimeError("cache unavailable")


class AuditSpy:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> object:
        self.records.append(kwargs)
        return kwargs


@pytest.mark.asyncio
async def test_cache_rate_limiter_blocks_after_fixed_window_limit() -> None:
    clock = Clock()
    cache = MemoryCacheProvider(clock=lambda: clock.now)
    audit = AuditSpy()
    limiter = CacheRateLimiter(cache, audit=audit)
    rule = RateLimitRule(
        name="files.upload",
        limit=2,
        window_seconds=60,
        dimensions=("tenant_id", "user_id", "route"),
    )
    identity = RateLimitIdentity(
        tenant_id="tenant-a",
        user_id="user-1",
        route="POST /files",
    )

    first = await limiter.check(rule, identity)
    second = await limiter.check(rule, identity)
    third = await limiter.check(rule, identity)

    assert first.allowed is True
    assert first.remaining == 1
    assert first.current == 1
    assert second.allowed is True
    assert second.remaining == 0
    assert third.allowed is False
    assert third.current == 3
    assert third.remaining == 0
    assert third.retry_after == 60
    assert third.headers == {"Retry-After": "60"}
    assert third.key == "rate:files.upload:tenant_id=tenant-a:user_id=user-1:route=POST /files"
    assert audit.records == [
        {
            "action": "rate_limit.hit",
            "resource_type": "route",
            "resource_id": "POST /files",
            "result": "denied",
            "tenant_id": "tenant-a",
            "actor_id": "user-1",
            "reason": "limit_exceeded",
            "payload": {
                "rule": "files.upload",
                "key": third.key,
                "limit": 2,
                "current": 3,
                "retry_after": 60,
            },
        }
    ]


@pytest.mark.asyncio
async def test_cache_rate_limiter_resets_after_window_expires() -> None:
    clock = Clock()
    cache = MemoryCacheProvider(clock=lambda: clock.now)
    limiter = CacheRateLimiter(cache)
    rule = RateLimitRule(
        name="auth.login",
        limit=1,
        window_seconds=10,
        dimensions=("ip_address", "route"),
    )
    identity = RateLimitIdentity(ip_address="127.0.0.1", route="POST /auth/login")

    assert (await limiter.check(rule, identity)).allowed is True
    assert (await limiter.check(rule, identity)).allowed is False

    clock.advance(11)

    reset = await limiter.check(rule, identity)
    assert reset.allowed is True
    assert reset.current == 1


@pytest.mark.asyncio
async def test_require_raises_rate_limited_with_retry_after_header() -> None:
    cache = MemoryCacheProvider()
    limiter = CacheRateLimiter(cache)
    rule = RateLimitRule(
        name="default.write",
        limit=1,
        window_seconds=30,
        dimensions=("user_id", "route"),
    )
    identity = RateLimitIdentity(user_id="user-1", route="POST /workspaces")

    await limiter.require(rule, identity)
    with pytest.raises(AppError) as limited:
        await limiter.require(rule, identity)

    assert limited.value.code == "RATE_LIMITED"
    assert limited.value.status_code == 429
    assert limited.value.headers == {"Retry-After": "30"}
    assert limited.value.details == {
        "rule": "default.write",
        "limit": 1,
        "current": 2,
        "remaining": 0,
        "retry_after": 30,
        "key": "rate:default.write:user_id=user-1:route=POST /workspaces",
    }


def test_registry_resolves_route_override_before_default_rule() -> None:
    default = RateLimitRule(
        name="default.write",
        limit=100,
        window_seconds=60,
        dimensions=("user_id", "route"),
    )
    login = RateLimitRule(
        name="auth.login",
        limit=5,
        window_seconds=60,
        dimensions=("ip_address", "route"),
    )
    registry = RateLimitRegistry(default_rule=default)
    registry.register_route("POST /auth/login", login)

    assert registry.resolve("POST /auth/login") is login
    assert registry.resolve("POST /files") is default


@pytest.mark.asyncio
async def test_cache_failure_policy_can_fail_open_or_fail_closed() -> None:
    fail_open = RateLimitRule(
        name="read.default",
        limit=10,
        window_seconds=60,
        dimensions=("user_id", "route"),
        fail_closed=False,
    )
    fail_closed = RateLimitRule(
        name="auth.login",
        limit=5,
        window_seconds=60,
        dimensions=("ip_address", "route"),
        fail_closed=True,
    )

    open_decision = await CacheRateLimiter(FailingCache()).check(
        fail_open,
        RateLimitIdentity(user_id="user-1", route="GET /files"),
    )

    assert open_decision.allowed is True
    assert open_decision.fail_open is True
    assert open_decision.reason == "cache_unavailable"

    with pytest.raises(AppError) as closed:
        await CacheRateLimiter(FailingCache()).require(
            fail_closed,
            RateLimitIdentity(ip_address="127.0.0.1", route="POST /auth/login"),
        )

    assert closed.value.code == "RATE_LIMITED"
    assert closed.value.details is not None
    assert closed.value.details["reason"] == "cache_unavailable"


def test_rate_limit_rule_validates_limit_window_and_dimensions() -> None:
    with pytest.raises(AppError) as invalid_limit:
        RateLimitRule(name="bad", limit=0, window_seconds=60, dimensions=("route",))
    with pytest.raises(AppError) as invalid_window:
        RateLimitRule(name="bad", limit=1, window_seconds=0, dimensions=("route",))
    with pytest.raises(AppError) as invalid_dimensions:
        RateLimitRule(name="bad", limit=1, window_seconds=60, dimensions=())

    assert invalid_limit.value.code == "VALIDATION_ERROR"
    assert invalid_window.value.code == "VALIDATION_ERROR"
    assert invalid_dimensions.value.code == "VALIDATION_ERROR"
