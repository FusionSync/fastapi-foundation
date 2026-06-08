from __future__ import annotations

import argparse
import asyncio
import json

from core.cli.common import print_payload
from core.config import get_settings
from core.mq import MqConsumeRequest, MqPublishRequest, RabbitMqClient
from core.rabbitmq import RabbitMqReadinessProbe, redact_rabbitmq_url


def register_mq_commands(subparsers: argparse._SubParsersAction) -> None:
    mq_parser = subparsers.add_parser("mq")
    mq_subparsers = mq_parser.add_subparsers(dest="mq_command", required=True)

    check_parser = mq_subparsers.add_parser("check")
    check_parser.add_argument("--url")
    check_parser.add_argument("--json", action="store_true", dest="as_json")
    check_parser.set_defaults(handler=_handle_mq_check)

    publish_parser = mq_subparsers.add_parser("publish-json")
    publish_parser.add_argument("--url")
    publish_parser.add_argument("--queue", required=True)
    publish_parser.add_argument("--routing-key")
    publish_parser.add_argument("--exchange", default="")
    publish_parser.add_argument("--payload-json", required=True)
    publish_parser.add_argument("--correlation-id")
    publish_parser.add_argument("--message-id")
    publish_parser.add_argument("--json", action="store_true", dest="as_json")
    publish_parser.set_defaults(handler=_handle_mq_publish_json)

    consume_parser = mq_subparsers.add_parser("consume-one")
    consume_parser.add_argument("--url")
    consume_parser.add_argument("--queue", required=True)
    consume_parser.add_argument("--timeout-seconds", type=float, default=1.0)
    consume_parser.add_argument("--json", action="store_true", dest="as_json")
    consume_parser.set_defaults(handler=_handle_mq_consume_one)


def _handle_mq_check(args: argparse.Namespace) -> int:
    url = _rabbitmq_url(args.url)
    client = RabbitMqClient(url)
    try:
        result = asyncio.run(RabbitMqReadinessProbe(client, url).check())
    finally:
        asyncio.run(client.close())
    payload = {
        "ok": result.ok,
        "command": "mq check",
        "provider": "rabbitmq",
        "url": redact_rabbitmq_url(url),
    }
    if result.error is not None:
        payload["error"] = result.error
    print_payload(payload, as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_mq_publish_json(args: argparse.Namespace) -> int:
    url = _rabbitmq_url(args.url)
    payload_json = _parse_payload_json(args.payload_json)
    client = RabbitMqClient(url)
    try:
        result = asyncio.run(
            client.publish(
                MqPublishRequest.json(
                    routing_key=args.routing_key or args.queue,
                    payload=payload_json,
                    exchange=args.exchange,
                    declare_queue=args.queue,
                    correlation_id=args.correlation_id,
                    message_id=args.message_id,
                )
            )
        )
    finally:
        asyncio.run(client.close())
    output = {
        **result.to_dict(),
        "command": "mq publish-json",
    }
    print_payload(output, as_json=args.as_json)
    return 0 if result.ok else 1


def _handle_mq_consume_one(args: argparse.Namespace) -> int:
    url = _rabbitmq_url(args.url)
    client = RabbitMqClient(url)
    try:
        message = asyncio.run(
            client.consume_one(
                MqConsumeRequest(
                    queue=args.queue,
                    timeout_seconds=args.timeout_seconds,
                )
            )
        )
    finally:
        asyncio.run(client.close())
    output = {
        "ok": True,
        "command": "mq consume-one",
        "provider": "rabbitmq",
        "queue": args.queue,
        "message": message.to_dict() if message is not None else None,
    }
    print_payload(output, as_json=args.as_json)
    return 0


def _rabbitmq_url(value: str | None) -> str:
    url = value or get_settings().dependencies.rabbitmq_url
    if not url:
        raise ValueError("RabbitMQ URL is not configured")
    return url


def _parse_payload_json(value: str) -> dict[str, object]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("payload-json must decode to an object")
    return dict(payload)
