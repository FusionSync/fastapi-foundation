import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.exceptions import (
    AppError,
    ErrorCodeSpec,
    ModuleErrorCode,
    define_module_error_codes,
    register_error_codes,
    register_exception_handlers,
)
from core.messages import (
    MessageCatalog,
    MessageRegistry,
    ModuleMessageCatalog,
    ModuleTranslationCatalog,
    TranslationCatalog,
    TranslationRegistry,
    define_module_message_catalogs,
    define_module_translation_catalogs,
    resolve_message,
    translate,
)
from core.messages import (
    gettext as _,
)
from core.serialization import fail


def test_default_message_resolver_uses_error_code_registry() -> None:
    assert resolve_message("PERMISSION_DENIED", locale="zh-CN") == "无权限访问该资源"
    assert resolve_message("PERMISSION_DENIED", locale="en-US") == "Permission denied"
    assert resolve_message("UNKNOWN_CODE", locale="zh-CN") == "系统错误"


def test_message_registry_registers_app_catalog_and_rejects_duplicates() -> None:
    register_error_codes(
        ErrorCodeSpec(
            "EXAMPLE_NOT_READY",
            409,
            "example is not ready",
            owner_module="example",
            details_schema={},
            deprecated=False,
        )
    )
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


def test_define_module_message_catalogs_requires_messages_or_exclusions() -> None:
    error_codes = define_module_error_codes(
        "orders",
        ModuleErrorCode("ORDERS_NOT_READY", 409, "orders are not ready"),
        ModuleErrorCode("ORDERS_LOCKED", 409, "orders are locked"),
    )

    with pytest.raises(ValueError, match="missing messages for codes: ORDERS_LOCKED"):
        define_module_message_catalogs(
            "orders",
            error_codes=error_codes,
            catalogs=[
                ModuleMessageCatalog(
                    locale="en-US",
                    messages={"ORDERS_NOT_READY": "Orders are not ready"},
                )
            ],
        )

    catalogs = define_module_message_catalogs(
        "orders",
        error_codes=error_codes,
        catalogs=[
            ModuleMessageCatalog(
                locale="en-US",
                messages={"ORDERS_NOT_READY": "Orders are not ready"},
                excluded_codes=["ORDERS_LOCKED"],
            )
        ],
    )

    assert catalogs == [
        MessageCatalog(
            locale="en-US",
            owner_module="orders",
            messages={"ORDERS_NOT_READY": "Orders are not ready"},
            excluded_codes=("ORDERS_LOCKED",),
        )
    ]


def test_message_registry_rejects_catalogs_without_matching_error_metadata() -> None:
    register_error_codes(
        ErrorCodeSpec(
            "EXAMPLE_MESSAGE_OWNER_MISMATCH",
            409,
            "example owner mismatch",
            owner_module="example",
            details_schema={"type": "object"},
            deprecated=False,
        ),
        ErrorCodeSpec(
            "EXAMPLE_MESSAGE_DEPRECATED",
            410,
            "deprecated example",
            owner_module="example",
            details_schema={},
            deprecated=True,
        ),
    )
    registry = MessageRegistry()

    with pytest.raises(AppError) as unknown:
        registry.register(
            MessageCatalog(
                locale="en-US",
                owner_module="example",
                messages={"EXAMPLE_MESSAGE_UNKNOWN": "Unknown"},
            )
        )
    with pytest.raises(AppError) as owner_mismatch:
        registry.register(
            MessageCatalog(
                locale="en-US",
                owner_module="other",
                messages={"EXAMPLE_MESSAGE_OWNER_MISMATCH": "Wrong owner"},
            )
        )
    with pytest.raises(AppError) as deprecated:
        registry.register(
            MessageCatalog(
                locale="en-US",
                owner_module="example",
                messages={"EXAMPLE_MESSAGE_DEPRECATED": "Deprecated"},
            )
        )

    assert unknown.value.details == {
        "code": "EXAMPLE_MESSAGE_UNKNOWN",
        "reason": "unregistered_error_code",
    }
    assert owner_mismatch.value.details == {
        "code": "EXAMPLE_MESSAGE_OWNER_MISMATCH",
        "expected_owner_module": "example",
        "owner_module": "other",
        "reason": "owner_mismatch",
    }
    assert deprecated.value.details == {
        "code": "EXAMPLE_MESSAGE_DEPRECATED",
        "reason": "deprecated_error_code",
    }


def test_message_registry_uses_language_fallback_before_default_message() -> None:
    register_error_codes(
        ErrorCodeSpec(
            "EXAMPLE_I18N_FALLBACK",
            409,
            "default fallback",
            owner_module="example",
            details_schema={},
            deprecated=False,
        )
    )
    registry = MessageRegistry()
    registry.register(
        MessageCatalog(
            locale="en-US",
            owner_module="example",
            messages={"EXAMPLE_I18N_FALLBACK": "English fallback"},
        )
    )

    assert registry.resolve("EXAMPLE_I18N_FALLBACK", locale="en-GB") == "English fallback"
    assert registry.resolve("EXAMPLE_I18N_FALLBACK", locale="fr-FR") == "default fallback"


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


def test_translation_registry_translates_source_strings_by_domain_and_locale() -> None:
    registry = TranslationRegistry()
    registry.register(
        TranslationCatalog(
            locale="zh-CN",
            domain="orders",
            owner_module="orders",
            messages={"Order {order_id} created": "订单 {order_id} 已创建"},
        )
    )

    assert registry.translate(
        "Order {order_id} created",
        locale="zh-CN",
        domain="orders",
        params={"order_id": "A001"},
    ) == "订单 A001 已创建"
    assert registry.translate(
        "Order {order_id} created",
        locale="en-US",
        domain="orders",
        params={"order_id": "A001"},
    ) == "Order A001 created"


def test_define_module_translation_catalogs_sets_owner_and_domain() -> None:
    catalogs = define_module_translation_catalogs(
        "orders",
        catalogs=[
            ModuleTranslationCatalog(
                locale="zh-CN",
                messages={"Create order": "创建订单"},
            )
        ],
    )

    assert catalogs == [
        TranslationCatalog(
            locale="zh-CN",
            domain="orders",
            owner_module="orders",
            messages={"Create order": "创建订单"},
        )
    ]


def test_global_translate_uses_registered_source_string_catalog() -> None:
    from core.messages import register_translation_catalogs

    register_translation_catalogs(
        TranslationCatalog(
            locale="zh-CN",
            domain="contract_test",
            owner_module="contract_test",
            messages={"Welcome, {name}": "欢迎，{name}"},
        )
    )

    assert translate(
        "Welcome, {name}",
        locale="zh-CN",
        domain="contract_test",
        params={"name": "Ada"},
    ) == "欢迎，Ada"
    assert _("Welcome, {name}", locale="zh-CN", domain="contract_test", params={"name": "Ada"}) == (
        "欢迎，Ada"
    )
