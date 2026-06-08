from __future__ import annotations

from dataclasses import dataclass

from core.events import EventEnvelope
from core.mq import MqClient, MqPublishRequest


@dataclass(frozen=True, slots=True)
class RabbitMqOutboxPublisher:
    mq_client: MqClient
    exchange: str = "foundation.events"
    routing_key_template: str = "{event_type}"
    declare_queue: str | None = None

    async def publish(self, envelope: EventEnvelope) -> None:
        await self.mq_client.publish(
            MqPublishRequest.json(
                exchange=self.exchange,
                routing_key=self.routing_key_template.format(
                    event_type=envelope.event_type,
                    event_version=envelope.event_version,
                    tenant_id=envelope.tenant_id,
                    aggregate_type=envelope.aggregate_type,
                    aggregate_id=envelope.aggregate_id,
                ),
                declare_queue=self.declare_queue,
                payload={
                    "event_id": envelope.event_id,
                    "event_type": envelope.event_type,
                    "event_version": envelope.event_version,
                    "tenant_id": envelope.tenant_id,
                    "aggregate_type": envelope.aggregate_type,
                    "aggregate_id": envelope.aggregate_id,
                    "payload": envelope.payload,
                },
                headers={
                    "event_id": envelope.event_id,
                    "event_type": envelope.event_type,
                    "event_version": envelope.event_version,
                    "tenant_id": envelope.tenant_id,
                },
                message_id=envelope.event_id,
                correlation_id=str(envelope.payload.get("request_id") or envelope.event_id),
            )
        )
