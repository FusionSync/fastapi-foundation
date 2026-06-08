from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any, Protocol


class _MqPublishJsonDescriptor:
    def __get__(self, instance: object, owner: type[MqPublishRequest]):
        if isinstance(instance, MqPublishRequest):
            return lambda: json.loads(instance.body.decode("utf-8"))

        def factory(
            *,
            routing_key: str,
            payload: Mapping[str, Any],
            exchange: str = "",
            declare_queue: str | None = None,
            headers: Mapping[str, object] | None = None,
            correlation_id: str | None = None,
            message_id: str | None = None,
            persistent: bool = True,
            mandatory: bool = True,
            durable_queue: bool = True,
            durable_exchange: bool = True,
        ) -> MqPublishRequest:
            body = json.dumps(
                dict(payload),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            return owner(
                routing_key=routing_key,
                body=body,
                exchange=exchange,
                declare_queue=declare_queue,
                content_type="application/json",
                headers=dict(headers or {}),
                correlation_id=correlation_id,
                message_id=message_id,
                persistent=persistent,
                mandatory=mandatory,
                durable_queue=durable_queue,
                durable_exchange=durable_exchange,
            )

        return factory


@dataclass(frozen=True, slots=True)
class MqPublishRequest:
    routing_key: str
    body: bytes
    exchange: str = ""
    declare_queue: str | None = None
    content_type: str = "application/octet-stream"
    headers: Mapping[str, object] = field(default_factory=dict)
    correlation_id: str | None = None
    message_id: str | None = None
    persistent: bool = True
    mandatory: bool = True
    durable_queue: bool = True
    durable_exchange: bool = True
    json = _MqPublishJsonDescriptor()


@dataclass(frozen=True, slots=True)
class MqConsumeRequest:
    queue: str
    timeout_seconds: float | None = 1.0
    auto_ack: bool = True
    durable_queue: bool = True


@dataclass(frozen=True, slots=True)
class MqPublishResult:
    ok: bool
    provider: str
    exchange: str
    routing_key: str
    message_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "exchange": self.exchange,
            "routing_key": self.routing_key,
            "message_id": self.message_id,
        }


@dataclass(frozen=True, slots=True)
class MqReceivedMessage:
    queue: str
    body: bytes
    content_type: str | None = None
    headers: Mapping[str, object] = field(default_factory=dict)
    message_id: str | None = None
    correlation_id: str | None = None
    exchange: str = ""
    routing_key: str = ""

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    def to_dict(self) -> dict[str, object]:
        body: object
        if self.content_type == "application/json":
            body = self.json()
        else:
            body = self.body.decode("utf-8", errors="replace")
        return {
            "body": body,
            "content_type": self.content_type,
            "headers": dict(self.headers),
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "exchange": self.exchange,
            "routing_key": self.routing_key,
        }


class MqClient(Protocol):
    async def check(self) -> None: ...

    async def publish(self, request: MqPublishRequest) -> MqPublishResult: ...

    async def consume_one(self, request: MqConsumeRequest) -> MqReceivedMessage | None: ...

    async def close(self) -> None: ...


RabbitMqConnectionFactory = Callable[[str], Awaitable[Any]]


class RabbitMqClient:
    def __init__(
        self,
        url: str,
        *,
        connection_factory: RabbitMqConnectionFactory | None = None,
    ) -> None:
        self.url = url
        self._connection_factory = connection_factory or _default_connection_factory
        self._connection: Any | None = None
        self._channel: Any | None = None

    async def check(self) -> None:
        await self._get_channel()

    async def publish(self, request: MqPublishRequest) -> MqPublishResult:
        channel = await self._get_channel()
        if request.declare_queue is not None:
            await channel.declare_queue(
                request.declare_queue,
                durable=request.durable_queue,
            )
        exchange = await self._exchange(channel, request)
        await exchange.publish(
            _message_from_request(request),
            routing_key=request.routing_key,
            mandatory=request.mandatory,
        )
        return MqPublishResult(
            ok=True,
            provider="rabbitmq",
            exchange=request.exchange,
            routing_key=request.routing_key,
            message_id=request.message_id,
        )

    async def consume_one(self, request: MqConsumeRequest) -> MqReceivedMessage | None:
        channel = await self._get_channel()
        queue = await channel.declare_queue(
            request.queue,
            durable=request.durable_queue,
        )
        incoming = await queue.get(timeout=request.timeout_seconds, fail=False)
        if incoming is None:
            return None
        received = MqReceivedMessage(
            queue=request.queue,
            body=bytes(incoming.body),
            content_type=getattr(incoming, "content_type", None),
            headers=dict(getattr(incoming, "headers", {}) or {}),
            message_id=getattr(incoming, "message_id", None),
            correlation_id=getattr(incoming, "correlation_id", None),
            exchange=getattr(incoming, "exchange", "") or "",
            routing_key=getattr(incoming, "routing_key", "") or "",
        )
        if request.auto_ack:
            await incoming.ack()
        return received

    async def close(self) -> None:
        if self._connection is None:
            return
        close = getattr(self._connection, "close", None)
        if close is None:
            return
        result = close()
        if isawaitable(result):
            await result
        self._connection = None
        self._channel = None

    async def _get_channel(self) -> Any:
        if self._connection is None or _is_closed(self._connection):
            self._connection = await self._connection_factory(self.url)
            self._channel = None
        if self._channel is None or _is_closed(self._channel):
            self._channel = await self._connection.channel()
        return self._channel

    async def _exchange(self, channel: Any, request: MqPublishRequest) -> Any:
        if not request.exchange:
            return channel.default_exchange
        return await channel.declare_exchange(
            request.exchange,
            durable=request.durable_exchange,
        )


async def _default_connection_factory(url: str) -> Any:
    try:
        from aio_pika import connect_robust
    except ImportError as exc:
        raise RuntimeError(
            "RabbitMQ runtime requires the aio-pika package. Install project dependencies again."
        ) from exc
    return await connect_robust(url)


def _message_from_request(request: MqPublishRequest) -> Any:
    try:
        from aio_pika import DeliveryMode, Message
    except ImportError as exc:
        raise RuntimeError(
            "RabbitMQ runtime requires the aio-pika package. Install project dependencies again."
        ) from exc
    return Message(
        body=request.body,
        content_type=request.content_type,
        headers=dict(request.headers),
        correlation_id=request.correlation_id,
        message_id=request.message_id,
        delivery_mode=(
            DeliveryMode.PERSISTENT
            if request.persistent
            else DeliveryMode.NOT_PERSISTENT
        ),
    )


def _is_closed(resource: Any) -> bool:
    closed = getattr(resource, "is_closed", False)
    if callable(closed):
        closed = closed()
    return bool(closed)
