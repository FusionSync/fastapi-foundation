from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from core.config import Settings
from core.mq import MqClient, RabbitMqClient
from core.operations import DependencyProbeResult

RabbitMqClientFactory = Callable[[str], MqClient]


@dataclass(frozen=True, slots=True)
class RabbitMqRuntimeDiagnostics:
    configured: bool
    url: str
    message_provider: str

    def to_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "url": self.url,
            "message_provider": self.message_provider,
        }


@dataclass(frozen=True, slots=True)
class RabbitMqRuntime:
    url: str
    client: MqClient

    async def dispose(self) -> None:
        await self.client.close()

    def diagnostics(self) -> RabbitMqRuntimeDiagnostics:
        return RabbitMqRuntimeDiagnostics(
            configured=True,
            url=redact_rabbitmq_url(self.url),
            message_provider="rabbitmq",
        )


class RabbitMqReadinessProbe:
    def __init__(self, client: MqClient, url: str) -> None:
        self.client = client
        self.url = url

    async def check(self) -> DependencyProbeResult:
        try:
            await self.client.check()
        except Exception as exc:
            return DependencyProbeResult(
                ok=False,
                details={
                    "service": "rabbitmq",
                    "target": redact_rabbitmq_url(self.url),
                },
                error=f"{type(exc).__name__}: {exc}",
            )
        return DependencyProbeResult(
            ok=True,
            details={
                "service": "rabbitmq",
                "target": redact_rabbitmq_url(self.url),
            },
        )


def create_rabbitmq_runtime(
    settings: Settings,
    *,
    client_factory: RabbitMqClientFactory | None = None,
) -> RabbitMqRuntime | None:
    rabbitmq_url = settings.dependencies.rabbitmq_url
    if not rabbitmq_url:
        return None
    client = (client_factory or RabbitMqClient)(rabbitmq_url)
    return RabbitMqRuntime(url=rabbitmq_url, client=client)


def redact_rabbitmq_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    if parsed.password is None:
        return url
    username = parsed.username or ""
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    netloc = f"{username}:***@{host}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
