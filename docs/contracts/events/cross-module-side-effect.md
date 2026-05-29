# Cross-module Event Side Effect Contract

This example shows the standard pattern for a reliable cross-module event:

1. The producer app declares an `EventSchemaSpec`.
2. The consumer app declares an `EventHandlerSpec`.
3. The producer writes through `OutboxEventPublisher` in the same transaction as business data.
4. The handler wraps each external call with `run_event_side_effect()`.

```python
from core.apps import EventHandlerSpec, EventSchemaSpec

event_schemas = [
    EventSchemaSpec(
        event_type="tenant.created",
        event_version=1,
        required_payload_fields=["tenant_name"],
        field_types={"tenant_name": "str"},
    )
]

event_handlers = [
    EventHandlerSpec(
        event_type="tenant.created",
        event_version=1,
        handler_path="apps.crm.events.sync_tenant_to_crm",
    )
]
```

Payload contract:

```json
{
  "tenant_id": "tenant-a",
  "actor_id": "user-1",
  "request_id": "req_123",
  "trace_id": "trace_123",
  "tenant_name": "Acme"
}
```

Handler contract:

```python
from core.events import EventEnvelope, run_event_side_effect


async def sync_tenant_to_crm(envelope: EventEnvelope) -> None:
    await run_event_side_effect(
        "crm.tenant.upsert",
        lambda: crm_client.upsert_tenant(
            idempotency_key=envelope.event_id,
            tenant_id=envelope.tenant_id,
            name=envelope.payload["tenant_name"],
        ),
        request_payload={"tenant_id": envelope.tenant_id},
    )
```

`run_event_side_effect()` uses the outbox dispatcher's `IdempotencyStore`. If the external call succeeds and the handler fails later, the next outbox retry replays the side-effect result and does not call the external system again.
