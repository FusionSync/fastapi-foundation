from datetime import UTC, datetime, timedelta

import pytest

from core.cache import MemoryCacheProvider, cache_key
from core.exceptions import AppError


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 5, 28, tzinfo=UTC)

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_memory_cache_requires_ttl_unless_explicitly_permanent() -> None:
    cache = MemoryCacheProvider()

    with pytest.raises(AppError) as missing_ttl:
        await cache.set("tenant:tenant-a:settings", {"plan": "pro"})

    await cache.set("tenant:tenant-a:settings", {"plan": "pro"}, permanent=True)

    assert missing_ttl.value.code == "VALIDATION_ERROR"
    assert await cache.get("tenant:tenant-a:settings") == {"plan": "pro"}


@pytest.mark.asyncio
async def test_memory_cache_expires_values_by_ttl() -> None:
    clock = Clock()
    cache = MemoryCacheProvider(clock=lambda: clock.now)

    await cache.set("auth:jwks:issuer-a", "jwks", ttl_seconds=10)
    assert await cache.exists("auth:jwks:issuer-a") is True

    clock.advance(11)

    assert await cache.get("auth:jwks:issuer-a") is None
    assert await cache.exists("auth:jwks:issuer-a") is False


@pytest.mark.asyncio
async def test_memory_cache_json_and_increment_share_ttl_semantics() -> None:
    clock = Clock()
    cache = MemoryCacheProvider(clock=lambda: clock.now)

    await cache.set_json("tenant:tenant-a:settings", {"features": ["files"]}, ttl_seconds=30)
    assert await cache.get_json("tenant:tenant-a:settings") == {"features": ["files"]}

    assert await cache.incr("rate:tenant-a:user-1:POST-files", ttl_seconds=60) == 1
    assert await cache.incr("rate:tenant-a:user-1:POST-files", ttl_seconds=60) == 2

    clock.advance(61)

    assert await cache.get_json("tenant:tenant-a:settings") is None
    assert await cache.get("rate:tenant-a:user-1:POST-files") is None


@pytest.mark.asyncio
async def test_memory_cache_expire_and_delete_return_whether_key_existed() -> None:
    clock = Clock()
    cache = MemoryCacheProvider(clock=lambda: clock.now)

    await cache.set("tenant:tenant-a:settings", "value", ttl_seconds=60)

    assert await cache.expire("tenant:tenant-a:settings", ttl_seconds=5) is True
    assert await cache.delete("missing:key") is False

    clock.advance(6)

    assert await cache.delete("tenant:tenant-a:settings") is False


def test_cache_key_builder_rejects_ambiguous_parts() -> None:
    assert cache_key("tenant", "tenant-a", "settings") == "tenant:tenant-a:settings"

    with pytest.raises(AppError) as empty_part:
        cache_key("tenant", "", "settings")
    with pytest.raises(AppError) as nested_separator:
        cache_key("tenant", "tenant:a", "settings")

    assert empty_part.value.code == "VALIDATION_ERROR"
    assert nested_separator.value.code == "VALIDATION_ERROR"
