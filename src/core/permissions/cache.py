from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote

from core.cache.keys import cache_key
from core.cache.provider import CacheProvider
from core.exceptions import AppError

if TYPE_CHECKING:
    from core.permissions.backends import PolicyMatch


class PermissionCacheInvalidator(Protocol):
    def invalidate(
        self,
        *,
        tenant_id: str | None = None,
        subject: str | None = None,
    ) -> object: ...


@dataclass(slots=True)
class PermissionCache:
    version: int = 0

    def invalidate(
        self,
        *,
        tenant_id: str | None = None,
        subject: str | None = None,
    ) -> None:
        self.version += 1


class DistributedPermissionCache:
    def __init__(
        self,
        provider: CacheProvider,
        *,
        ttl_seconds: int = 60,
    ) -> None:
        if ttl_seconds <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "Permission cache TTL must be greater than zero",
                status_code=400,
            )
        self.provider = provider
        self.ttl_seconds = ttl_seconds

    async def current_version(self, *, tenant_id: str, subject: str) -> int:
        return await self._version(self._subject_version_key(tenant_id=tenant_id, subject=subject))

    async def invalidate(
        self,
        *,
        tenant_id: str | None = None,
        subject: str | None = None,
    ) -> int:
        if subject is not None and tenant_id is None:
            raise AppError(
                "VALIDATION_ERROR",
                "Permission subject cache invalidation requires tenant_id",
                status_code=400,
            )
        if tenant_id is not None and subject is not None:
            return await self.provider.incr(
                self._subject_version_key(tenant_id=tenant_id, subject=subject),
                permanent=True,
            )
        if tenant_id is not None:
            return await self.provider.incr(
                self._tenant_version_key(tenant_id=tenant_id),
                permanent=True,
            )
        return await self.provider.incr(self._global_version_key(), permanent=True)

    async def get_allowing_match(
        self,
        *,
        tenant_id: str,
        subject: str,
        resource: str,
        action: str,
    ) -> PolicyMatch | None:
        from core.permissions.backends import PolicyMatch

        cached = await self.provider.get_json(
            self._decision_key(
                tenant_id=tenant_id,
                subject=subject,
                resource=resource,
                action=action,
            )
        )
        if not isinstance(cached, dict):
            return None
        marker = await self._version_marker(tenant_id=tenant_id, subject=subject)
        if cached.get("version") != marker:
            return None
        match = cached.get("match")
        if not isinstance(match, dict):
            return None
        return PolicyMatch.from_dict(match)

    async def set_allowing_match(self, match: PolicyMatch) -> None:
        marker = await self._version_marker(tenant_id=match.tenant_id, subject=match.subject)
        await self.provider.set_json(
            self._decision_key(
                tenant_id=match.tenant_id,
                subject=match.subject,
                resource=match.resource,
                action=match.action,
            ),
            {
                "version": marker,
                "match": match.to_dict(),
            },
            ttl_seconds=self.ttl_seconds,
        )

    async def _version_marker(self, *, tenant_id: str, subject: str) -> dict[str, int]:
        return {
            "global": await self._version(self._global_version_key()),
            "tenant": await self._version(self._tenant_version_key(tenant_id=tenant_id)),
            "subject": await self.current_version(tenant_id=tenant_id, subject=subject),
        }

    async def _version(self, key: str) -> int:
        version = await self.provider.get(key)
        if version is None:
            return 0
        if not isinstance(version, int):
            raise AppError(
                "CONFLICT",
                "Permission cache version is not an integer",
                status_code=409,
            )
        return version

    def _decision_key(
        self,
        *,
        tenant_id: str,
        subject: str,
        resource: str,
        action: str,
    ) -> str:
        return cache_key(
            "permission",
            _part("tenant_id", tenant_id),
            _part("subject", subject),
            _part("resource", resource),
            _part("action", action),
            "decision",
        )

    def _global_version_key(self) -> str:
        return cache_key("permission", "policy_version")

    def _tenant_version_key(self, *, tenant_id: str) -> str:
        return cache_key("permission", _part("tenant_id", tenant_id), "policy_version")

    def _subject_version_key(self, *, tenant_id: str, subject: str) -> str:
        return cache_key(
            "permission",
            _part("tenant_id", tenant_id),
            _part("subject", subject),
            "policy_version",
        )


async def invalidate_permission_cache(
    cache: PermissionCacheInvalidator,
    *,
    tenant_id: str | None = None,
    subject: str | None = None,
) -> None:
    result = cache.invalidate(tenant_id=tenant_id, subject=subject)
    if isawaitable(result):
        await result


def _part(name: str, value: str) -> str:
    if not value.strip():
        raise AppError(
            "VALIDATION_ERROR",
            "Permission cache key parts must be non-empty",
            status_code=400,
        )
    return f"{name}={quote(value, safe='')}"
