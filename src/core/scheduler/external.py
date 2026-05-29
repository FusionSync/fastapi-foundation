from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.scheduler.provider import (
    ScheduleTriggerProvider,
    ScheduleTriggerRequest,
    ScheduleTriggerResult,
)
from core.scheduler.registry import ScheduleRegistry
from core.tenancy import TenantStatus


@dataclass(frozen=True, slots=True)
class ExternalScheduleJob:
    provider: str
    schedule_id: str
    task_type: str
    tenant_id: str
    trigger: str
    trigger_config: dict[str, str]
    misfire_policy: str
    request_id_prefix: str
    payload: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "schedule_id": self.schedule_id,
            "task_type": self.task_type,
            "tenant_id": self.tenant_id,
            "trigger": self.trigger,
            "trigger_config": self.trigger_config,
            "misfire_policy": self.misfire_policy,
            "request_id_prefix": self.request_id_prefix,
            "payload": self.payload,
        }


class APSchedulerScheduleProvider:
    provider_name = "apscheduler"

    def __init__(
        self,
        *,
        schedule_registry: ScheduleRegistry,
        trigger_provider: ScheduleTriggerProvider,
    ) -> None:
        self.schedule_registry = schedule_registry
        self.trigger_provider = trigger_provider

    async def trigger(
        self,
        request: ScheduleTriggerRequest,
        *,
        tenant_status: TenantStatus = "active",
    ) -> ScheduleTriggerResult:
        result = await self.trigger_provider.trigger(request, tenant_status=tenant_status)
        result.metadata["scheduler_provider"] = self.provider_name
        return result

    def job_specs(
        self,
        *,
        tenant_id: str,
        request_id_prefix: str,
        payload: dict[str, object] | None = None,
    ) -> tuple[ExternalScheduleJob, ...]:
        return _external_jobs(
            provider=self.provider_name,
            schedule_registry=self.schedule_registry,
            tenant_id=tenant_id,
            request_id_prefix=request_id_prefix,
            payload=payload,
        )


class CeleryBeatScheduleProvider:
    provider_name = "celery_beat"

    def __init__(
        self,
        *,
        schedule_registry: ScheduleRegistry,
        trigger_provider: ScheduleTriggerProvider,
        task_name: str = "core.scheduler.trigger",
    ) -> None:
        self.schedule_registry = schedule_registry
        self.trigger_provider = trigger_provider
        self.task_name = task_name

    async def trigger(
        self,
        request: ScheduleTriggerRequest,
        *,
        tenant_status: TenantStatus = "active",
    ) -> ScheduleTriggerResult:
        result = await self.trigger_provider.trigger(request, tenant_status=tenant_status)
        result.metadata["scheduler_provider"] = self.provider_name
        return result

    def beat_schedule(
        self,
        *,
        tenant_id: str,
        request_id_prefix: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, dict[str, Any]]:
        entries: dict[str, dict[str, Any]] = {}
        for job in self._jobs(
            tenant_id=tenant_id,
            request_id_prefix=request_id_prefix,
            payload=payload,
        ):
            entries[job.schedule_id] = {
                "task": self.task_name,
                "schedule": {
                    "trigger": job.trigger,
                    "trigger_config": job.trigger_config,
                },
                "kwargs": {
                    "schedule_id": job.schedule_id,
                    "tenant_id": job.tenant_id,
                    "request_id_prefix": job.request_id_prefix,
                    "payload": job.payload,
                    "misfire_policy": job.misfire_policy,
                },
            }
        return entries

    def _jobs(
        self,
        *,
        tenant_id: str,
        request_id_prefix: str,
        payload: dict[str, object] | None,
    ) -> tuple[ExternalScheduleJob, ...]:
        return _external_jobs(
            provider=self.provider_name,
            schedule_registry=self.schedule_registry,
            tenant_id=tenant_id,
            request_id_prefix=request_id_prefix,
            payload=payload,
        )


def wrap_external_scheduler_provider(
    *,
    provider: str,
    schedule_registry: ScheduleRegistry,
    trigger_provider: ScheduleTriggerProvider,
) -> ScheduleTriggerProvider:
    if provider == "apscheduler":
        return APSchedulerScheduleProvider(
            schedule_registry=schedule_registry,
            trigger_provider=trigger_provider,
        )
    if provider == "celery_beat":
        return CeleryBeatScheduleProvider(
            schedule_registry=schedule_registry,
            trigger_provider=trigger_provider,
        )
    if provider == "local":
        return trigger_provider
    raise ValueError(f"Unknown scheduler provider: {provider!r}")


def _external_jobs(
    *,
    provider: str,
    schedule_registry: ScheduleRegistry,
    tenant_id: str,
    request_id_prefix: str,
    payload: dict[str, object] | None,
) -> tuple[ExternalScheduleJob, ...]:
    jobs: list[ExternalScheduleJob] = []
    for registered in schedule_registry.registered_schedules:
        if registered.spec.trigger == "manual":
            continue
        jobs.append(
            ExternalScheduleJob(
                provider=provider,
                schedule_id=registered.spec.schedule_id,
                task_type=registered.spec.task_type,
                tenant_id=tenant_id,
                trigger=registered.spec.trigger,
                trigger_config=dict(registered.spec.trigger_config),
                misfire_policy=registered.spec.misfire_policy,
                request_id_prefix=request_id_prefix,
                payload=dict(payload or {}),
            )
        )
    return tuple(jobs)
