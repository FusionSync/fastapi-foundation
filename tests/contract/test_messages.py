import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.exceptions import AppError, register_exception_handlers
from core.messages import MessageCatalog, MessageRegistry, resolve_message
from core.serialization import fail


def test_default_message_resolver_uses_error_code_registry() -> None:
    assert resolve_message("PERMISSION_DENIED", locale="zh-CN") == "无权限访问该资源"
    assert resolve_message("PERMISSION_DENIED", locale="en-US") == "Permission denied"
    assert resolve_message("UNKNOWN_CODE", locale="zh-CN") == "系统错误"


def test_message_registry_registers_app_catalog_and_rejects_duplicates() -> None:
    registry = MessageRegistry()
    registry.register(
        MessageCatalog(
            locale="en-US",
            owner_module="example",
            messages={"EXAMPLE_NOT_READY": "Example is not ready"},
        )
    )

    assert registry.resolve("EXAMPLE_NOT_READY", locale="en-US") == "Example is not ready"

    with pytest.raises(AppError) as duplicate:
        registry.register(
            MessageCatalog(
                locale="en-US",
                owner_module="another",
                messages={"EXAMPLE_NOT_READY": "Duplicate"},
            )
        )

    assert duplicate.value.code == "VALIDATION_ERROR"


def test_message_catalog_rejects_sensitive_message_text() -> None:
    with pytest.raises(AppError) as rejected:
        MessageCatalog(
            locale="zh-CN",
            owner_module="bad",
            messages={"BAD": "password token secret"},
        )

    assert rejected.value.code == "VALIDATION_ERROR"
    assert rejected.value.details == {"code": "BAD", "reason": "sensitive_message"}


def test_fail_envelope_resolves_message_when_not_explicit() -> None:
    response = fail("QUOTA_EXCEEDED", locale="en-US", request_id="req_test")

    assert response["code"] == "QUOTA_EXCEEDED"
    assert response["message"] == "Quota exceeded"
    assert response["request_id"] == "req_test"


def test_exception_handler_uses_resolved_message_unless_error_message_is_explicit() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/default-message")
    async def default_message() -> None:
        raise AppError("PERMISSION_DENIED")

    @app.get("/explicit-message")
    async def explicit_message() -> None:
        raise AppError("PERMISSION_DENIED", "custom denied")

    client = TestClient(app)

    default_response = client.get("/default-message")
    explicit_response = client.get("/explicit-message")

    assert default_response.status_code == 403
    assert default_response.json()["message"] == "无权限访问该资源"
    assert explicit_response.status_code == 403
    assert explicit_response.json()["message"] == "custom denied"
