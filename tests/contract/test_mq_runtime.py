import json

import pytest
from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings
from core.mq import (
    MqConsumeRequest,
    MqPublishRequest,
    MqPublishResult,
    MqReceivedMessage,
    RabbitMqClient,
)
from core.rabbitmq import RabbitMqReadinessProbe, create_rabbitmq_runtime


class FakeMqClient:
    def __init__(self) -> None:
        self.check_count = 0
        self.closed = False

    async def check(self) -> None:
        self.check_count += 1

    async def close(self) -> None:
        self.closed = True


class FakeCliMqClient:
    instances = []

    def __init__(self, url: str) -> None:
        self.url = url
        self.published_request = None
        self.closed = False
        self.instances.append(self)

    async def check(self) -> None:
        return None

    async def publish(self, request: MqPublishRequest) -> MqPublishResult:
        self.published_request = request
        return MqPublishResult(
            ok=True,
            provider="rabbitmq",
            exchange=request.exchange,
            routing_key=request.routing_key,
            message_id=request.message_id,
        )

    async def consume_one(self, request: MqConsumeRequest) -> MqReceivedMessage:
        assert request.queue == "foundation.test"
        assert request.timeout_seconds == 1
        return MqReceivedMessage(
            queue=request.queue,
            body=b'{"hello":"world"}',
            content_type="application/json",
            headers={"source": "test"},
            message_id="msg-1",
            correlation_id="corr-1",
            exchange="",
            routing_key="foundation.test",
        )

    async def close(self) -> None:
        self.closed = True


def test_mq_json_publish_request_encodes_payload_and_headers() -> None:
    request = MqPublishRequest.json(
        routing_key="foundation.events",
        payload={"event": "tenant.created", "tenant_id": "tenant-1"},
        declare_queue="foundation.events",
        headers={"tenant_id": "tenant-1"},
        correlation_id="corr-1",
        message_id="msg-1",
    )

    assert request.routing_key == "foundation.events"
    assert request.declare_queue == "foundation.events"
    assert request.content_type == "application/json"
    assert request.headers == {"tenant_id": "tenant-1"}
    assert request.correlation_id == "corr-1"
    assert request.message_id == "msg-1"
    assert json.loads(request.body.decode("utf-8")) == {
        "event": "tenant.created",
        "tenant_id": "tenant-1",
    }


def test_rabbitmq_runtime_is_disabled_without_url() -> None:
    assert create_rabbitmq_runtime(Settings()) is None


@pytest.mark.asyncio
async def test_rabbitmq_runtime_wires_readiness_diagnostics_and_disposal() -> None:
    client = FakeMqClient()
    runtime = create_rabbitmq_runtime(
        Settings(
            dependencies={
                "rabbitmq_url": "amqp://ui:secret@rabbitmq.internal:5672/%2F",
            }
        ),
        client_factory=lambda _url: client,
    )

    assert runtime is not None
    assert runtime.client is client
    assert runtime.diagnostics().to_dict() == {
        "configured": True,
        "url": "amqp://ui:***@rabbitmq.internal:5672/%2F",
        "message_provider": "rabbitmq",
    }

    result = await RabbitMqReadinessProbe(runtime.client, runtime.url).check()

    assert result.ok is True
    assert result.details == {
        "service": "rabbitmq",
        "target": "amqp://ui:***@rabbitmq.internal:5672/%2F",
    }
    assert client.check_count == 1

    await runtime.dispose()

    assert client.closed is True


def test_create_app_wires_configured_rabbitmq_runtime_into_readiness() -> None:
    mq_client = FakeMqClient()
    app = create_app(
        Settings(
            database={"url": "sqlite+aiosqlite:///:memory:"},
            dependencies={"rabbitmq_url": "amqp://ui:secret@127.0.0.1:5672/%2F"},
        ),
        rabbitmq_client_factory=lambda _url: mq_client,
    )
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["checks"]["rabbitmq_reachable"] is True
    assert body["details"]["dependencies"]["rabbitmq"] == {
        "ok": True,
        "details": {
            "service": "rabbitmq",
            "target": "amqp://ui:***@127.0.0.1:5672/%2F",
        },
    }
    assert app.state.startup_diagnostics["providers"]["rabbitmq"] == {
        "ok": True,
        "details": {
            "configured": True,
            "url": "amqp://ui:***@127.0.0.1:5672/%2F",
            "message_provider": "rabbitmq",
        },
    }
    assert body["details"]["startup_diagnostics"]["providers"]["rabbitmq"] == {
        "ok": True,
        "details": {
            "service": "rabbitmq",
            "target": "amqp://ui:***@127.0.0.1:5672/%2F",
        },
    }
    assert app.state.rabbitmq_client is mq_client
    assert app.state.rabbitmq_runtime is not None
    assert mq_client.check_count == 1


def test_mq_cli_check_reports_redacted_broker_url(monkeypatch, capsys) -> None:
    from core.cli.main import main

    monkeypatch.setattr("core.cli.mq.RabbitMqClient", FakeCliMqClient)

    exit_code = main(
        [
            "mq",
            "check",
            "--url",
            "amqp://ui:secret@127.0.0.1:5672/%2F",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {
        "ok": True,
        "command": "mq check",
        "provider": "rabbitmq",
        "url": "amqp://ui:***@127.0.0.1:5672/%2F",
    }


def test_mq_cli_publish_and_consume_json(monkeypatch, capsys) -> None:
    from core.cli.main import main

    FakeCliMqClient.instances.clear()
    monkeypatch.setattr("core.cli.mq.RabbitMqClient", FakeCliMqClient)

    publish_exit_code = main(
        [
            "mq",
            "publish-json",
            "--url",
            "amqp://ui:secret@127.0.0.1:5672/%2F",
            "--queue",
            "foundation.test",
            "--payload-json",
            '{"hello":"world"}',
            "--json",
        ]
    )
    publish_payload = json.loads(capsys.readouterr().out)

    consume_exit_code = main(
        [
            "mq",
            "consume-one",
            "--url",
            "amqp://ui:secret@127.0.0.1:5672/%2F",
            "--queue",
            "foundation.test",
            "--timeout-seconds",
            "1",
            "--json",
        ]
    )
    consume_payload = json.loads(capsys.readouterr().out)

    assert publish_exit_code == 0
    assert publish_payload == {
        "ok": True,
        "command": "mq publish-json",
        "provider": "rabbitmq",
        "exchange": "",
        "routing_key": "foundation.test",
        "message_id": None,
    }
    assert FakeCliMqClient.instances[0].published_request is not None
    assert FakeCliMqClient.instances[0].published_request.declare_queue == "foundation.test"
    assert json.loads(
        FakeCliMqClient.instances[0].published_request.body.decode("utf-8")
    ) == {"hello": "world"}
    assert consume_exit_code == 0
    assert consume_payload == {
        "ok": True,
        "command": "mq consume-one",
        "provider": "rabbitmq",
        "queue": "foundation.test",
        "message": {
            "body": {"hello": "world"},
            "content_type": "application/json",
            "headers": {"source": "test"},
            "message_id": "msg-1",
            "correlation_id": "corr-1",
            "exchange": "",
            "routing_key": "foundation.test",
        },
    }


@pytest.mark.asyncio
async def test_rabbitmq_client_publishes_and_consumes_one_message() -> None:
    broker = FakeRabbitMqBroker()
    client = RabbitMqClient(
        "amqp://guest:guest@localhost:5672/%2F",
        connection_factory=broker.connect,
    )
    publish_request = MqPublishRequest.json(
        routing_key="foundation.test",
        payload={"hello": "world"},
        declare_queue="foundation.test",
    )

    publish_result = await client.publish(publish_request)
    received = await client.consume_one(
        MqConsumeRequest(queue="foundation.test", timeout_seconds=1),
    )

    assert publish_result.ok is True
    assert publish_result.provider == "rabbitmq"
    assert publish_result.routing_key == "foundation.test"
    assert broker.channel.declared_queues == [
        ("foundation.test", True),
        ("foundation.test", True),
    ]
    assert broker.channel.default_exchange.published[0].routing_key == "foundation.test"
    assert received is not None
    assert received.queue == "foundation.test"
    assert received.content_type == "application/json"
    assert received.json() == {"hello": "world"}
    assert broker.channel.queue.message.acked is True

    await client.close()

    assert broker.connection.closed is True


class FakeRabbitMqBroker:
    def __init__(self) -> None:
        self.channel = FakeRabbitMqChannel()
        self.connection = FakeRabbitMqConnection(self.channel)

    async def connect(self, _url: str):
        return self.connection


class FakeRabbitMqConnection:
    def __init__(self, channel) -> None:
        self.channel_obj = channel
        self.closed = False

    async def channel(self):
        return self.channel_obj

    async def close(self) -> None:
        self.closed = True


class FakeRabbitMqChannel:
    def __init__(self) -> None:
        self.default_exchange = FakeRabbitMqExchange()
        self.queue = FakeRabbitMqQueue()
        self.declared_queues = []

    async def declare_queue(self, name: str, *, durable: bool, **_kwargs):
        self.declared_queues.append((name, durable))
        return self.queue


class FakeRabbitMqExchange:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, message, *, routing_key: str, mandatory: bool):
        self.published.append(
            FakeRabbitMqPublishedMessage(
                body=message.body,
                content_type=message.content_type,
                headers=message.headers,
                routing_key=routing_key,
                mandatory=mandatory,
            )
        )


class FakeRabbitMqQueue:
    def __init__(self) -> None:
        self.message = FakeRabbitMqIncomingMessage(
            body=b'{"hello":"world"}',
            content_type="application/json",
            headers={"source": "test"},
            message_id="msg-1",
            correlation_id="corr-1",
            routing_key="foundation.test",
        )

    async def get(self, *, timeout: float | None, fail: bool):
        assert timeout == 1
        assert fail is False
        return self.message


class FakeRabbitMqIncomingMessage:
    def __init__(
        self,
        *,
        body: bytes,
        content_type: str,
        headers: dict[str, str],
        message_id: str,
        correlation_id: str,
        routing_key: str,
    ) -> None:
        self.body = body
        self.content_type = content_type
        self.headers = headers
        self.message_id = message_id
        self.correlation_id = correlation_id
        self.exchange = ""
        self.routing_key = routing_key
        self.acked = False

    async def ack(self) -> None:
        self.acked = True


class FakeRabbitMqPublishedMessage:
    def __init__(
        self,
        *,
        body: bytes,
        content_type: str,
        headers: dict[str, str],
        routing_key: str,
        mandatory: bool,
    ) -> None:
        self.body = body
        self.content_type = content_type
        self.headers = headers
        self.routing_key = routing_key
        self.mandatory = mandatory
