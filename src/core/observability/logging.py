from __future__ import annotations

import logging
from typing import Any

from core.context.context import RequestContext

request_logger = logging.getLogger("core.observability.requests")


def log_http_request(
    *,
    context: RequestContext | None,
    status_code: int,
    app_code: str,
    duration_ms: float,
    settings: Any,
) -> None:
    request_logger.info(
        "http_request_completed",
        extra={
            "http_request": http_request_log_fields(
                context=context,
                status_code=status_code,
                app_code=app_code,
                duration_ms=duration_ms,
                settings=settings,
            )
        },
    )


def http_request_log_fields(
    *,
    context: RequestContext | None,
    status_code: int,
    app_code: str,
    duration_ms: float,
    settings: Any,
) -> dict[str, object]:
    app_settings = getattr(settings, "app", None)
    observability_settings = getattr(settings, "observability", None)
    return {
        "request_id": context.request_id if context is not None else None,
        "trace_id": context.trace_id if context is not None else None,
        "tenant_id": context.tenant_id if context is not None else None,
        "user_id": context.user_id if context is not None else None,
        "route": context.route if context is not None else None,
        "method": context.method if context is not None else None,
        "status_code": status_code,
        "status_class": f"{status_code // 100}xx",
        "app_code": app_code,
        "duration_ms": duration_ms,
        "deployment_mode": getattr(app_settings, "env", None),
        "service_role": getattr(observability_settings, "service_role", None),
        "instance_id": getattr(observability_settings, "instance_id", None),
        "version": getattr(app_settings, "version", None),
        "ip_address": context.ip_address if context is not None else None,
        "user_agent": context.user_agent if context is not None else None,
    }
