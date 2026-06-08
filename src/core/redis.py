from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from core.cache import RedisCacheProvider
from core.config import Settings
from core.locks import RedisLockProvider
from core.operations import DependencyProbeResult


class RedisClient(Protocol):
    async def ping(self) -> Any: ...


RedisClientFactory = Callable[[str], RedisClient]


@dataclass(frozen=True, slots=True)
class RedisRuntimeDiagnostics:
    configured: bool
    url: str
    cache_provider: str
    lock_provider: str

    def to_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "url": self.url,
            "cache_provider": self.cache_provider,
            "lock_provider": self.lock_provider,
        }


@dataclass(frozen=True, slots=True)
class RedisRuntime:
    url: str
    client: RedisClient
    cache_provider: RedisCacheProvider
    lock_provider: RedisLockProvider

    async def dispose(self) -> None:
        await _close_redis_client(self.client)

    def diagnostics(self) -> RedisRuntimeDiagnostics:
        return RedisRuntimeDiagnostics(
            configured=True,
            url=_redact_url(self.url),
            cache_provider="redis",
            lock_provider="redis",
        )


class RedisReadinessProbe:
    def __init__(self, client: RedisClient, url: str) -> None:
        self.client = client
        self.url = url

    async def check(self) -> DependencyProbeResult:
        try:
            await self.client.ping()
        except Exception as exc:
            return DependencyProbeResult(
                ok=False,
                details={
                    "service": "redis",
                    "target": _redact_url(self.url),
                },
                error=f"{type(exc).__name__}: {exc}",
            )
        return DependencyProbeResult(
            ok=True,
            details={
                "service": "redis",
                "target": _redact_url(self.url),
            },
        )


def create_redis_runtime(
    settings: Settings,
    *,
    client_factory: RedisClientFactory | None = None,
) -> RedisRuntime | None:
    redis_url = settings.dependencies.redis_url
    if not redis_url:
        return None
    client = (client_factory or _default_redis_client_factory)(redis_url)
    return RedisRuntime(
        url=redis_url,
        client=client,
        cache_provider=RedisCacheProvider(client),
        lock_provider=RedisLockProvider(client),
    )


def _default_redis_client_factory(redis_url: str) -> RedisClient:
    try:
        from redis.asyncio import Redis
    except ImportError as exc:
        raise RuntimeError(
            "Redis runtime requires the redis package. Install project dependencies again."
        ) from exc
    return Redis.from_url(redis_url, decode_responses=False)


async def _close_redis_client(client: RedisClient) -> None:
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if isawaitable(result):
        await result


def _redact_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    if parsed.password is None:
        return url
    username = parsed.username or ""
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    netloc = f"{username}:***@{host}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
