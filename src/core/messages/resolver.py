from __future__ import annotations

from core.exceptions.base import AppError
from core.exceptions.codes import (
    get_error_code,
    is_error_code_registered,
    iter_error_codes,
    require_error_code,
)
from core.messages.catalog import MessageCatalog

DEFAULT_LOCALE = "zh-CN"

_EN_US_CORE_MESSAGES = {
    "AUTH_INVALID_TOKEN": "Authentication failed",
    "CONFLICT": "Resource conflict",
    "EXTERNAL_SERVICE_ERROR": "External service error",
    "IDEMPOTENCY_IN_PROGRESS": "Request is already processing",
    "IDEMPOTENCY_KEY_CONFLICT": "Idempotency key conflict",
    "LOCK_NOT_ACQUIRED": "Resource is already processing",
    "NOT_FOUND": "Resource not found",
    "PERMISSION_DENIED": "Permission denied",
    "QUOTA_EXCEEDED": "Quota exceeded",
    "RATE_LIMITED": "Too many requests",
    "SYSTEM_ERROR": "System error",
    "TASK_IDEMPOTENCY_KEY_CONFLICT": "Task idempotency key conflict",
    "TENANT_ACCESS_DENIED": "Tenant access denied",
    "TENANT_CONTEXT_CONFLICT": "Tenant context conflict",
    "TENANT_STATE_FORBIDDEN": "Tenant state does not allow this operation",
    "UPLOAD_REJECTED": "Upload rejected",
    "USER_DISABLED": "User is disabled",
    "VALIDATION_ERROR": "Validation failed",
}


class MessageRegistry:
    def __init__(self) -> None:
        self._messages: dict[str, dict[str, str]] = {}

    def register(
        self,
        catalog: MessageCatalog,
        *,
        replace: bool = False,
        validate_codes: bool = True,
    ) -> None:
        if validate_codes:
            _validate_catalog_codes(catalog)
        locale_messages = self._messages.setdefault(catalog.locale, {})
        duplicates = sorted(
            code
            for code in set(locale_messages).intersection(catalog.messages)
            if locale_messages[code] != catalog.messages[code]
        )
        if duplicates and not replace:
            raise AppError(
                "VALIDATION_ERROR",
                "duplicate message code in locale",
                status_code=400,
                details={"locale": catalog.locale, "codes": duplicates},
            )
        locale_messages.update(catalog.messages)

    def resolve(self, code: str, *, locale: str | None = None) -> str:
        resolved_locale = locale or DEFAULT_LOCALE
        for candidate_locale in self._locale_fallbacks(resolved_locale):
            locale_messages = self._messages.get(candidate_locale, {})
            if code in locale_messages:
                return locale_messages[code]
        default_messages = self._messages.get(DEFAULT_LOCALE, {})
        if code in default_messages:
            return default_messages[code]
        return get_error_code(code).default_message

    def to_dict(self) -> dict[str, dict[str, str]]:
        return {locale: dict(messages) for locale, messages in self._messages.items()}

    def _locale_fallbacks(self, locale: str) -> tuple[str, ...]:
        candidates = [locale]
        language = locale.split("-", 1)[0]
        for registered_locale in sorted(self._messages):
            if registered_locale == locale:
                continue
            if registered_locale.split("-", 1)[0] == language:
                candidates.append(registered_locale)
        return tuple(candidates)


def default_message_registry() -> MessageRegistry:
    registry = MessageRegistry()
    registry.register(
        MessageCatalog(
            locale="zh-CN",
            owner_module="core",
            messages={spec.code: spec.default_message for spec in iter_error_codes()},
        ),
        validate_codes=False,
    )
    registry.register(
        MessageCatalog(
            locale="en-US",
            owner_module="core",
            messages=_EN_US_CORE_MESSAGES,
        ),
        validate_codes=False,
    )
    return registry


_DEFAULT_REGISTRY = default_message_registry()


def register_message_catalogs(*catalogs: MessageCatalog, replace: bool = False) -> None:
    for catalog in catalogs:
        _DEFAULT_REGISTRY.register(catalog, replace=replace)


def resolve_message(code: str, *, locale: str | None = None) -> str:
    return _DEFAULT_REGISTRY.resolve(code, locale=locale)


def _validate_catalog_codes(catalog: MessageCatalog) -> None:
    for code in catalog.messages:
        if not is_error_code_registered(code):
            raise AppError(
                "VALIDATION_ERROR",
                "message catalog code must be registered in error code registry",
                status_code=400,
                details={"code": code, "reason": "unregistered_error_code"},
            )
        spec = require_error_code(code)
        if spec.owner_module != catalog.owner_module:
            raise AppError(
                "VALIDATION_ERROR",
                "message catalog owner must match error code owner",
                status_code=400,
                details={
                    "code": code,
                    "expected_owner_module": spec.owner_module,
                    "owner_module": catalog.owner_module,
                    "reason": "owner_mismatch",
                },
            )
        if spec.deprecated:
            raise AppError(
                "VALIDATION_ERROR",
                "message catalog cannot target deprecated error code",
                status_code=400,
                details={"code": code, "reason": "deprecated_error_code"},
            )
