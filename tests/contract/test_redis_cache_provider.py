import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from core.cache import RedisCacheProvider
from core.exceptions import AppError


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 5, 28, tzinfo=UTC)

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass(slots=True)
class _RedisEntry:
    value: str
    expires_at: datetime | None = None


class FakeRedis:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.values: dict[str, _RedisEntry] = {}

    async def get(self, name: str) -> str | None:
        self._purge_if_expired(name)
        entry = self.values.get(name)
        return entry.value if entry else None

    async def set(self, name: str, value: str, *, ex: int | None = None) -> bool:
        expires_at = (
            self.clock.now + timedelta(seconds=ex)
            if ex is not None
            else None
        )
        self.values[name] = _RedisEntry(value, expires_at=expires_at)
        return True

    async def delete(self, name: str) -> int:
        self._purge_if_expired(name)
        return 1 if self.values.pop(name, None) is not None else 0

    async def exists(self, name: str) -> int:
        self._purge_if_expired(name)
        return 1 if name in self.values else 0

    async def incrby(self, name: str, amount: int) -> int:
        self._purge_if_expired(name)
        current = self.values.get(name)
        next_value = int(current.value if current else 0) + amount
        self.values[name] = _RedisEntry(
            str(next_value),
            expires_at=current.expires_at if current else None,
        )
        return next_value

    async def expire(self, name: str, seconds: int) -> bool:
        self._purge_if_expired(name)
        entry = self.values.get(name)
        if entry is None:
            return False
        entry.expires_at = self.clock.now + timedelta(seconds=seconds)
        return True

    def _purge_if_expired(self, name: str) -> None:
        entry = self.values.get(name)
        if (
            entry is not None
            and entry.expires_at is not None
            and entry.expires_at <= self.clock.now
        ):
            self.values.pop(name, None)


@pytest.mark.asyncio
async def test_redis_cache_requires_ttl_unless_explicitly_permanent() -> None:
    cache = RedisCacheProvider(FakeRedis(Clock()))

    with pytest.raises(AppError) as missing_ttl:
        await cache.set("tenant:tenant-a:settings", "value")

    await cache.set("tenant:tenant-a:settings", "value", permanent=True)

    assert missing_ttl.value.code == "VALIDATION_ERROR"
    assert await cache.get("tenant:tenant-a:settings") == "value"


@pytest.mark.asyncio
async def test_redis_cache_expires_values_by_ttl() -> None:
    clock = Clock()
    cache = RedisCacheProvider(FakeRedis(clock))

    await cache.set("auth:jwks:issuer-a", "jwks", ttl_seconds=10)
    assert await cache.exists("auth:jwks:issuer-a") is True

    clock.advance(11)

    assert await cache.get("auth:jwks:issuer-a") is None
    assert await cache.exists("auth:jwks:issuer-a") is False


@pytest.mark.asyncio
async def test_redis_cache_json_and_increment_share_ttl_semantics() -> None:
    clock = Clock()
    cache = RedisCacheProvider(FakeRedis(clock))

    await cache.set_json("tenant:tenant-a:settings", {"features": ["files"]}, ttl_seconds=30)
    assert await cache.get_json("tenant:tenant-a:settings") == {"features": ["files"]}

    assert await cache.incr("rate:tenant-a:user-1:POST-files", ttl_seconds=60) == 1
    assert await cache.incr("rate:tenant-a:user-1:POST-files", ttl_seconds=60) == 2

    clock.advance(61)

    assert await cache.get_json("tenant:tenant-a:settings") is None
    assert await cache.get("rate:tenant-a:user-1:POST-files") is None


@pytest.mark.asyncio
async def test_redis_cache_expire_delete_and_conflict_handling() -> None:
    clock = Clock()
    redis = FakeRedis(clock)
    cache = RedisCacheProvider(redis)

    await cache.set("tenant:tenant-a:settings", "value", ttl_seconds=60)
    await redis.set("rate:bad", json.dumps({"not": "an int"}), ex=60)

    assert await cache.expire("tenant:tenant-a:settings", ttl_seconds=5) is True
    assert await cache.delete("missing:key") is False
    with pytest.raises(AppError) as conflict:
        await cache.incr("rate:bad", ttl_seconds=60)

    clock.advance(6)

    assert conflict.value.code == "CONFLICT"
    assert await cache.delete("tenant:tenant-a:settings") is False
