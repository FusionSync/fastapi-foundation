import pytest

from core.cache import RedisCacheProvider
from core.config import Settings
from core.locks import RedisLockProvider
from core.redis import RedisReadinessProbe, create_redis_runtime


class FakeRedisClient:
    def __init__(self) -> None:
        self.ping_count = 0
        self.closed = False

    async def ping(self) -> bool:
        self.ping_count += 1
        return True

    async def aclose(self) -> None:
        self.closed = True


def test_redis_runtime_is_disabled_without_url() -> None:
    assert create_redis_runtime(Settings()) is None


@pytest.mark.asyncio
async def test_redis_runtime_wires_cache_lock_readiness_and_disposal() -> None:
    client = FakeRedisClient()
    runtime = create_redis_runtime(
        Settings(dependencies={"redis_url": "redis://:secret@127.0.0.1:6379/0"}),
        client_factory=lambda _url: client,
    )

    assert runtime is not None
    assert runtime.client is client
    assert isinstance(runtime.cache_provider, RedisCacheProvider)
    assert isinstance(runtime.lock_provider, RedisLockProvider)
    assert runtime.diagnostics().to_dict() == {
        "configured": True,
        "url": "redis://:***@127.0.0.1:6379/0",
        "cache_provider": "redis",
        "lock_provider": "redis",
    }

    result = await RedisReadinessProbe(runtime.client, runtime.url).check()

    assert result.ok is True
    assert result.details == {
        "service": "redis",
        "target": "redis://:***@127.0.0.1:6379/0",
    }
    assert client.ping_count == 1

    await runtime.dispose()

    assert client.closed is True
