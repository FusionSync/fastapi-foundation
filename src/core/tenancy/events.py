from __future__ import annotations

from collections.abc import Mapping

from core.outbox import OutboxRepository
from core.tenancy.models import Tenant

TENANT_CREATED_EVENT = "tenant.created"
TENANT_SUSPENDED_EVENT = "tenant.suspended"
TENANT_REACTIVATED_EVENT = "tenant.reactivated"
TENANT_DELETING_EVENT = "tenant.deleting"
TENANT_ARCHIVED_EVENT = "tenant.archived"
TENANT_DELETED_EVENT = "tenant.deleted"

TENANT_LIFECYCLE_EVENTS = (
    TENANT_CREATED_EVENT,
    TENANT_SUSPENDED_EVENT,
    TENANT_REACTIVATED_EVENT,
    TENANT_DELETING_EVENT,
    TENANT_ARCHIVED_EVENT,
    TENANT_DELETED_EVENT,
)


async def publish_tenant_lifecycle_event(
    outbox: OutboxRepository,
    event_type: str,
    *,
    tenant: Tenant,
    actor_id: str,
    request_id: str,
    extra: Mapping[str, str],
) -> None:
    await outbox.add(
        event_type=event_type,
        aggregate_type="tenant",
        aggregate_id=tenant.id,
        tenant_id=tenant.id,
        payload={
            "tenant_id": tenant.id,
            "actor_id": actor_id,
            "request_id": request_id,
            "status": tenant.status,
            **dict(extra),
        },
    )
