from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.cache import MemoryCacheProvider
from core.exceptions import AppError
from core.observability import MetricsRegistry
from core.rate_limit import (
    CacheRateLimiter,
    RateLimitIdentity,
    RateLimitMiddleware,
    RateLimitRegistry,
    RateLimitRule,
    SlidingWindowRateLimiter,
)
from core.serialization import ok


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
    metrics = MetricsRegistry()
    limiter = CacheRateLimiter(cache, audit=audit, metrics=metrics)
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
    assert (
        'rate_limit_hits_total{reason="limit_exceeded",route="POST /files",rule="files.upload"} 1'
        in metrics.render()
    )


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
async def test_sliding_window_rate_limiter_counts_previous_window_weight() -> None:
    clock = Clock()
    cache = MemoryCacheProvider(clock=lambda: clock.now)
    limiter = SlidingWindowRateLimiter(cache, clock=lambda: clock.now)
    rule = RateLimitRule(
        name="auth.login",
        limit=2,
        window_seconds=10,
        dimensions=("ip_address", "route"),
    )
    identity = RateLimitIdentity(ip_address="127.0.0.1", route="POST /auth/login")

    assert (await limiter.check(rule, identity)).allowed is True
    assert (await limiter.check(rule, identity)).allowed is True
    clock.advance(11)

    decision = await limiter.check(rule, identity)

    assert decision.allowed is False
    assert decision.current == 3
    assert decision.remaining == 0
    assert decision.retry_after == 9
    assert decision.reason == "limit_exceeded"


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


def test_rate_limit_middleware_blocks_request_with_retry_after_envelope() -> None:
    app = FastAPI()
    metrics = MetricsRegistry()
    registry = RateLimitRegistry(
        default_rule=RateLimitRule(
            name="default.write",
            limit=1,
            window_seconds=30,
            dimensions=("ip_address", "route"),
        )
    )
    app.state.rate_limit_registry = registry
    app.state.rate_limiter = CacheRateLimiter(MemoryCacheProvider(), metrics=metrics)
    app.add_middleware(RateLimitMiddleware)

    @app.post("/workspaces")
    async def create_workspace() -> dict[str, object]:
        return ok({"created": True}, request_id="req_test")

    client = TestClient(app)

    first = client.post("/workspaces")
    second = client.post("/workspaces")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "30"
    assert second.headers["X-App-Code"] == "RATE_LIMITED"
    assert second.json()["code"] == "RATE_LIMITED"
    assert second.json()["details"] == {
        "rule": "default.write",
        "limit": 1,
        "current": 2,
        "remaining": 0,
        "retry_after": 30,
        "key": "rate:default.write:ip_address=testclient:route=POST /workspaces",
    }
    assert (
        'rate_limit_hits_total{reason="limit_exceeded",route="POST /workspaces",'
        'rule="default.write"} 1'
    ) in metrics.render()


def test_rate_limit_middleware_does_not_use_tenant_header_without_context() -> None:
    app = FastAPI()
    registry = RateLimitRegistry(
        default_rule=RateLimitRule(
            name="tenant.write",
            limit=1,
            window_seconds=30,
            dimensions=("tenant_id", "route"),
        )
    )
    app.state.rate_limit_registry = registry
    app.state.rate_limiter = CacheRateLimiter(MemoryCacheProvider())
    app.add_middleware(RateLimitMiddleware)

    @app.post("/workspaces")
    async def create_workspace() -> dict[str, object]:
        return ok({"created": True}, request_id="req_test")

    client = TestClient(app)

    first = client.post("/workspaces", headers={"X-Tenant-ID": "tenant-a"})
    second = client.post("/workspaces", headers={"X-Tenant-ID": "tenant-a"})

    assert first.status_code == 200
    assert second.status_code == 200


def test_rate_limit_middleware_uses_route_override_before_default_rule() -> None:
    app = FastAPI()
    registry = RateLimitRegistry(
        default_rule=RateLimitRule(
            name="default",
            limit=100,
            window_seconds=60,
            dimensions=("ip_address", "route"),
        )
    )
    registry.register_route(
        "POST /auth/login",
        RateLimitRule(
            name="auth.login",
            limit=1,
            window_seconds=10,
            dimensions=("ip_address", "route"),
        ),
    )
    app.state.rate_limit_registry = registry
    app.state.rate_limiter = CacheRateLimiter(MemoryCacheProvider())
    app.add_middleware(RateLimitMiddleware)

    @app.post("/auth/login")
    async def login() -> dict[str, object]:
        return ok({"status": "ok"}, request_id="req_test")

    client = TestClient(app)

    assert client.post("/auth/login").status_code == 200
    limited = client.post("/auth/login")

    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "10"
    assert limited.json()["details"]["rule"] == "auth.login"
