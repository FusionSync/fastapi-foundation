import pytest

from core.context import RequestContext, reset_current_context, set_current_context
from core.exceptions import AppError
from core.http_clients import (
    CoreHttpClient,
    ExternalServiceAppError,
    HttpClientConfig,
    HttpResponse,
    MockHttpTransport,
    RetryConfig,
)


@pytest.mark.asyncio
async def test_core_http_client_injects_context_headers_timeout_and_user_agent() -> None:
    transport = MockHttpTransport(
        responses=[
            HttpResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                json_body={"issuer": "https://oidc.example"},
            )
        ]
    )
    client = CoreHttpClient(
        HttpClientConfig(
            service_name="oidc",
            base_url="https://oidc.example",
            timeout_seconds=2.5,
            user_agent="service-core/0.1",
        ),
        transport=transport,
    )
    token = set_current_context(RequestContext(request_id="req-1", trace_id="trace-1"))
    try:
        response = await client.get("/.well-known/openid-configuration")
    finally:
        reset_current_context(token)

    assert response.json_body == {"issuer": "https://oidc.example"}
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == "https://oidc.example/.well-known/openid-configuration"
    assert request.timeout_seconds == 2.5
    assert request.headers["User-Agent"] == "service-core/0.1"
    assert request.headers["X-Request-ID"] == "req-1"
    assert request.headers["X-Trace-ID"] == "trace-1"


@pytest.mark.asyncio
async def test_core_http_client_retries_configured_statuses_then_succeeds() -> None:
    transport = MockHttpTransport(
        responses=[
            HttpResponse(status_code=503, headers={}, text="try later"),
            HttpResponse(status_code=200, headers={}, json_body={"ok": True}),
        ]
    )
    client = CoreHttpClient(
        HttpClientConfig(
            service_name="webhook",
            base_url="https://hooks.example",
            retry=RetryConfig(max_attempts=2, retry_statuses=(503,)),
        ),
        transport=transport,
    )

    response = await client.post_json("/deliver", json_body={"event": "tenant.created"})

    assert response.status_code == 200
    assert response.json_body == {"ok": True}
    assert [request.method for request in transport.requests] == ["POST", "POST"]


@pytest.mark.asyncio
async def test_core_http_client_converts_http_failure_to_redacted_external_error() -> None:
    transport = MockHttpTransport(
        responses=[
            HttpResponse(
                status_code=500,
                headers={},
                json_body={"error": "failed", "access_token": "secret-token"},
            )
        ]
    )
    client = CoreHttpClient(
        HttpClientConfig(service_name="ai", base_url="https://ai.example"),
        transport=transport,
    )
    token = set_current_context(RequestContext(request_id="req-ai"))
    try:
        with pytest.raises(ExternalServiceAppError) as failed:
            await client.post_json(
                "/chat",
                json_body={"prompt": "hello", "password": "secret-password"},
            )
    finally:
        reset_current_context(token)

    assert failed.value.code == "EXTERNAL_SERVICE_ERROR"
    assert failed.value.status_code == 502
    assert failed.value.details == {
        "service_name": "ai",
        "method": "POST",
        "url": "https://ai.example/chat",
        "upstream_status_code": 500,
        "request_id": "req-ai",
        "request_body": {"prompt": "hello", "password": "***REDACTED***"},
        "response_body": {"error": "failed", "access_token": "***REDACTED***"},
    }


@pytest.mark.asyncio
async def test_core_http_client_converts_transport_failure_to_external_error() -> None:
    transport = MockHttpTransport(responses=[TimeoutError("network timeout")])
    client = CoreHttpClient(
        HttpClientConfig(
            service_name="sms",
            base_url="https://sms.example",
            retry=RetryConfig(max_attempts=1),
        ),
        transport=transport,
    )

    with pytest.raises(ExternalServiceAppError) as failed:
        await client.get("/send")

    assert failed.value.code == "EXTERNAL_SERVICE_ERROR"
    assert failed.value.details is not None
    assert failed.value.details["service_name"] == "sms"
    assert failed.value.details["error_type"] == "TimeoutError"


def test_http_client_config_validates_timeout_retry_and_service_name() -> None:
    with pytest.raises(AppError) as invalid_timeout:
        HttpClientConfig(service_name="oidc", base_url="https://oidc.example", timeout_seconds=0)
    with pytest.raises(AppError) as invalid_attempts:
        RetryConfig(max_attempts=0)
    with pytest.raises(AppError) as invalid_service:
        HttpClientConfig(service_name="", base_url="https://oidc.example")

    assert invalid_timeout.value.code == "VALIDATION_ERROR"
    assert invalid_attempts.value.code == "VALIDATION_ERROR"
    assert invalid_service.value.code == "VALIDATION_ERROR"


def test_mock_http_transport_requires_enough_scripted_responses() -> None:
    transport = MockHttpTransport(responses=[])

    with pytest.raises(AppError) as exhausted:
        transport.next_response()

    assert exhausted.value.code == "VALIDATION_ERROR"
