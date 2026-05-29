import sys
import types
from typing import Any

import pytest

from core.apps import AppModule, AppRegistry, TaskHandlerSpec
from core.exceptions import AppError
from core.quotas import (
    MemoryQuotaUsageStore,
    QuotaMutationGate,
    QuotaReservation,
    QuotaRule,
    QuotaService,
    QuotaSubject,
    QuotaTaskSubmitter,
)
from core.tasks import SyncTaskProvider, TaskEnvelope, TaskRegistry


@pytest.mark.asyncio
async def test_quota_mutation_gate_releases_business_reservations_when_mutation_fails() -> None:
    store = MemoryQuotaUsageStore()
    rule = QuotaRule(metric="active_users", limit=3, scope="tenant")
    subject = QuotaSubject(tenant_id="tenant-a")
    gate = QuotaMutationGate(QuotaService(store))

    async def create_member() -> dict[str, str]:
        raise RuntimeError("database write failed")

    with pytest.raises(RuntimeError, match="database write failed"):
        await gate.run_mutation(
            [QuotaReservation(rule=rule, subject=subject, amount=1)],
            create_member,
        )

    assert await store.get_usage(rule.key_for(subject)) == 0


@pytest.mark.asyncio
async def test_quota_mutation_gate_rolls_back_earlier_reservations_when_later_rule_denies() -> None:
    store = MemoryQuotaUsageStore()
    subject = QuotaSubject(tenant_id="tenant-a")
    file_count = QuotaRule(metric="file_count", limit=5, scope="tenant")
    storage_bytes = QuotaRule(metric="storage_bytes", limit=4, scope="tenant")
    gate = QuotaMutationGate(QuotaService(store))
    calls: list[str] = []

    async def upload() -> dict[str, str]:
        calls.append("upload")
        return {"ok": "true"}

    with pytest.raises(AppError) as exceeded:
        await gate.run_mutation(
            [
                QuotaReservation(rule=file_count, subject=subject, amount=1),
                QuotaReservation(rule=storage_bytes, subject=subject, amount=8),
            ],
            upload,
        )

    assert exceeded.value.code == "QUOTA_EXCEEDED"
    assert calls == []
    assert await store.get_usage(file_count.key_for(subject)) == 0
    assert await store.get_usage(storage_bytes.key_for(subject)) == 0


@pytest.mark.asyncio
async def test_quota_task_submitter_reserves_before_submit_and_blocks_when_limit_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def refresh(envelope: TaskEnvelope) -> dict[str, str]:
        calls.append(envelope.task_id)
        return {"task_id": envelope.task_id}

    store = MemoryQuotaUsageStore()
    subject = QuotaSubject(tenant_id="tenant-a")
    rule = QuotaRule(metric="submitted_tasks", limit=1, scope="tenant")
    submitter = QuotaTaskSubmitter(
        SyncTaskProvider(_task_registry(monkeypatch, refresh=refresh)),
        quota_gate=QuotaMutationGate(QuotaService(store)),
        reservations_for_envelope=lambda envelope: [
            QuotaReservation(rule=rule, subject=QuotaSubject(tenant_id=envelope.tenant_id))
        ],
    )

    first = await submitter.submit(
        TaskEnvelope(
            task_id="task-quota-1",
            task_type="example.refresh",
            tenant_id="tenant-a",
            payload={},
            idempotency_key="example.refresh:tenant-a:quota-1",
            request_id="req-1",
        )
    )
    with pytest.raises(AppError) as exceeded:
        await submitter.submit(
            TaskEnvelope(
                task_id="task-quota-2",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={},
                idempotency_key="example.refresh:tenant-a:quota-2",
                request_id="req-2",
            )
        )

    assert first.ok is True
    assert first.task_id == "task-quota-1"
    assert exceeded.value.code == "QUOTA_EXCEEDED"
    assert calls == ["task-quota-1"]
    assert await store.get_usage(rule.key_for(subject)) == 1


@pytest.mark.asyncio
async def test_quota_task_submitter_releases_reservations_when_submit_raises() -> None:
    class FailingSubmitter:
        async def submit(
            self,
            envelope: TaskEnvelope,
            *,
            tenant_status: str = "active",
        ) -> Any:
            raise RuntimeError(f"queue unavailable for {envelope.task_id}")

    store = MemoryQuotaUsageStore()
    subject = QuotaSubject(tenant_id="tenant-a")
    rule = QuotaRule(metric="submitted_tasks", limit=1, scope="tenant")
    submitter = QuotaTaskSubmitter(
        FailingSubmitter(),
        quota_gate=QuotaMutationGate(QuotaService(store)),
        reservations_for_envelope=lambda envelope: [
            QuotaReservation(rule=rule, subject=QuotaSubject(tenant_id=envelope.tenant_id))
        ],
    )

    with pytest.raises(RuntimeError, match="queue unavailable for task-quota-failed"):
        await submitter.submit(
            TaskEnvelope(
                task_id="task-quota-failed",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={},
                idempotency_key="example.refresh:tenant-a:failed",
                request_id="req-failed",
            )
        )

    assert await store.get_usage(rule.key_for(subject)) == 0


def _task_registry(
    monkeypatch: pytest.MonkeyPatch,
    **handlers: Any,
) -> TaskRegistry:
    handler_module = types.ModuleType("fake_quota_task_handlers")
    for name, handler in handlers.items():
        setattr(handler_module, name, handler)
    app_module = types.ModuleType("fake_quota_task_app")
    app_module.module = AppModule(
        label="quota_task_app",
        version="0.1.0",
        task_handlers=[
            TaskHandlerSpec(
                task_type="example.refresh",
                handler_path="fake_quota_task_handlers.refresh",
            )
        ],
    )
    monkeypatch.setitem(sys.modules, "fake_quota_task_handlers", handler_module)
    monkeypatch.setitem(sys.modules, "fake_quota_task_app", app_module)
    return TaskRegistry.from_app_registry(AppRegistry(["fake_quota_task_app"]).load())
