from typing import Any

import pytest

from core.exceptions import AppError
from core.observability import MetricsRegistry
from core.quotas import (
    MemoryQuotaUsageStore,
    QuotaRegistry,
    QuotaRule,
    QuotaService,
    QuotaSubject,
)


class AuditSpy:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> object:
        self.records.append(kwargs)
        return kwargs


def test_quota_registry_builds_tenant_rules_from_config() -> None:
    registry = QuotaRegistry.from_tenant_config(
        tenant_id="tenant-a",
        config={
            "storage_bytes": 1024,
            "file_count": {"limit": 20, "scope": "tenant"},
            "concurrent_tasks": {"limit": 3, "scope": "user"},
        },
    )

    storage = registry.resolve("storage_bytes")
    tasks = registry.resolve("concurrent_tasks")

    assert storage == QuotaRule(metric="storage_bytes", limit=1024, scope="tenant")
    assert tasks == QuotaRule(metric="concurrent_tasks", limit=3, scope="user")
    assert registry.tenant_id == "tenant-a"


@pytest.mark.asyncio
async def test_quota_check_is_read_only_and_reports_remaining_capacity() -> None:
    store = MemoryQuotaUsageStore()
    service = QuotaService(store)
    rule = QuotaRule(metric="storage_bytes", limit=100, scope="tenant")
    subject = QuotaSubject(tenant_id="tenant-a")

    await store.set_usage(rule.key_for(subject), 80)

    decision = await service.check(rule, subject, amount=15)

    assert decision.allowed is True
    assert decision.current == 80
    assert decision.projected == 95
    assert decision.remaining == 5
    assert decision.key == "quota:storage_bytes:tenant_id=tenant-a"
    assert await store.get_usage(decision.key) == 80


@pytest.mark.asyncio
async def test_quota_reserve_increments_usage_until_limit_and_audits_exhaustion() -> None:
    store = MemoryQuotaUsageStore()
    audit = AuditSpy()
    metrics = MetricsRegistry()
    service = QuotaService(store, audit=audit, metrics=metrics)
    rule = QuotaRule(metric="file_count", limit=2, scope="tenant")
    subject = QuotaSubject(tenant_id="tenant-a")

    first = await service.reserve(rule, subject, amount=1)
    second = await service.reserve(rule, subject, amount=1)
    denied = await service.reserve(rule, subject, amount=1)

    assert first.allowed is True
    assert second.allowed is True
    assert await store.get_usage(rule.key_for(subject)) == 2
    assert denied.allowed is False
    assert denied.current == 2
    assert denied.projected == 3
    assert denied.remaining == 0
    assert await store.get_usage(rule.key_for(subject)) == 2
    assert audit.records == [
        {
            "action": "quota.exceeded",
            "resource_type": "quota",
            "resource_id": "file_count",
            "result": "denied",
            "tenant_id": "tenant-a",
            "actor_id": None,
            "reason": "quota_exceeded",
            "payload": {
                "metric": "file_count",
                "scope": "tenant",
                "key": "quota:file_count:tenant_id=tenant-a",
                "limit": 2,
                "current": 2,
                "requested": 1,
                "projected": 3,
            },
        }
    ]
    assert 'quota_exceeded_total{metric="file_count",scope="tenant"} 1' in metrics.render()


@pytest.mark.asyncio
async def test_quota_require_reserve_raises_stable_error_details() -> None:
    store = MemoryQuotaUsageStore()
    service = QuotaService(store)
    rule = QuotaRule(metric="active_users", limit=1, scope="tenant")
    subject = QuotaSubject(tenant_id="tenant-a", user_id="owner-1")

    await service.require_reserve(rule, subject, amount=1)
    with pytest.raises(AppError) as exceeded:
        await service.require_reserve(rule, subject, amount=1)

    assert exceeded.value.code == "QUOTA_EXCEEDED"
    assert exceeded.value.status_code == 403
    assert exceeded.value.details == {
        "metric": "active_users",
        "scope": "tenant",
        "limit": 1,
        "current": 1,
        "requested": 1,
        "projected": 2,
        "remaining": 0,
        "key": "quota:active_users:tenant_id=tenant-a",
    }


@pytest.mark.asyncio
async def test_quota_release_decrements_without_going_negative() -> None:
    store = MemoryQuotaUsageStore()
    service = QuotaService(store)
    rule = QuotaRule(metric="concurrent_tasks", limit=2, scope="user")
    subject = QuotaSubject(tenant_id="tenant-a", user_id="user-1")

    await service.reserve(rule, subject, amount=2)

    assert await service.release(rule, subject, amount=1) == 1
    assert await service.release(rule, subject, amount=10) == 0
    assert await store.get_usage(rule.key_for(subject)) == 0


def test_quota_rule_and_subject_validate_required_fields() -> None:
    with pytest.raises(AppError) as invalid_limit:
        QuotaRule(metric="storage_bytes", limit=-1, scope="tenant")
    with pytest.raises(AppError) as invalid_metric:
        QuotaRule(metric="bad:metric", limit=1, scope="tenant")
    with pytest.raises(AppError) as missing_user:
        QuotaRule(metric="concurrent_tasks", limit=1, scope="user").key_for(
            QuotaSubject(tenant_id="tenant-a")
        )
    with pytest.raises(AppError) as missing_resource:
        QuotaRule(metric="resource_usage", limit=1, scope="resource").key_for(
            QuotaSubject(tenant_id="tenant-a", resource_type="project")
        )

    assert invalid_limit.value.code == "VALIDATION_ERROR"
    assert invalid_metric.value.code == "VALIDATION_ERROR"
    assert missing_user.value.code == "VALIDATION_ERROR"
    assert missing_resource.value.code == "VALIDATION_ERROR"
