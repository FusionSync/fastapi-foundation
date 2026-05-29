import pytest

from core.cache import (
    CacheInvalidationHandler,
    MemoryCacheProvider,
    permission_cache_key,
    permission_role_grant_cache_key,
    permission_subject_cache_key,
    register_cache_invalidation_handlers,
    tenant_lifecycle_cache_key,
    tenant_settings_cache_key,
)
from core.events import EventEnvelope, EventRegistry


@pytest.mark.asyncio
async def test_permission_event_invalidates_tenant_subject_and_grant_cache_keys() -> None:
    cache = MemoryCacheProvider()
    await cache.set(permission_cache_key("tenant-a"), "tenant-policy", permanent=True)
    await cache.set(
        permission_subject_cache_key("tenant-a", "user", "user-1"),
        "subject-policy",
        permanent=True,
    )
    await cache.set(
        permission_role_grant_cache_key("tenant-a", "grant-1"),
        "grant-policy",
        permanent=True,
    )
    await cache.set(permission_cache_key("tenant-b"), "other-tenant-policy", permanent=True)

    result = await CacheInvalidationHandler(cache).handle(
        EventEnvelope(
            event_id="event-1",
            event_type="permissions.role_grant_changed",
            event_version=1,
            tenant_id="tenant-a",
            aggregate_type="role_grant",
            aggregate_id="grant-1",
            payload={
                "tenant_id": "tenant-a",
                "actor_id": "owner-1",
                "request_id": "req-1",
                "grant_id": "grant-1",
                "subject_type": "user",
                "subject_id": "user-1",
            },
        )
    )

    assert result.deleted_keys == (
        permission_cache_key("tenant-a"),
        permission_role_grant_cache_key("tenant-a", "grant-1"),
        permission_subject_cache_key("tenant-a", "user", "user-1"),
    )
    assert await cache.get(permission_cache_key("tenant-a")) is None
    assert await cache.get(permission_subject_cache_key("tenant-a", "user", "user-1")) is None
    assert await cache.get(permission_role_grant_cache_key("tenant-a", "grant-1")) is None
    assert await cache.get(permission_cache_key("tenant-b")) == "other-tenant-policy"


@pytest.mark.asyncio
async def test_tenant_lifecycle_event_invalidates_tenant_and_permission_cache_keys() -> None:
    cache = MemoryCacheProvider()
    await cache.set_json(tenant_settings_cache_key("tenant-a"), {"plan": "pro"}, permanent=True)
    await cache.set(tenant_lifecycle_cache_key("tenant-a"), "active", permanent=True)
    await cache.set(permission_cache_key("tenant-a"), "tenant-policy", permanent=True)

    result = await CacheInvalidationHandler(cache).handle(
        EventEnvelope(
            event_id="event-2",
            event_type="tenant.suspended",
            event_version=1,
            tenant_id="tenant-a",
            aggregate_type="tenant",
            aggregate_id="tenant-a",
            payload={
                "tenant_id": "tenant-a",
                "actor_id": "owner-1",
                "request_id": "req-2",
            },
        )
    )

    assert result.deleted_keys == (
        tenant_settings_cache_key("tenant-a"),
        tenant_lifecycle_cache_key("tenant-a"),
        permission_cache_key("tenant-a"),
    )
    assert await cache.get_json(tenant_settings_cache_key("tenant-a")) is None
    assert await cache.get(tenant_lifecycle_cache_key("tenant-a")) is None
    assert await cache.get(permission_cache_key("tenant-a")) is None


@pytest.mark.asyncio
async def test_default_cache_invalidation_rules_register_with_event_registry() -> None:
    cache = MemoryCacheProvider()
    await cache.set(tenant_lifecycle_cache_key("tenant-a"), "suspended", permanent=True)
    registry = EventRegistry()

    register_cache_invalidation_handlers(registry, CacheInvalidationHandler(cache))

    await registry.dispatch(
        EventEnvelope(
            event_id="event-3",
            event_type="tenant.reactivated",
            event_version=1,
            tenant_id="tenant-a",
            aggregate_type="tenant",
            aggregate_id="tenant-a",
            payload={
                "tenant_id": "tenant-a",
                "actor_id": "owner-1",
                "request_id": "req-3",
            },
        )
    )

    assert await cache.get(tenant_lifecycle_cache_key("tenant-a")) is None
