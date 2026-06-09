from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.exceptions.base import AppError
from core.exceptions.codes import ErrorCodeSpec
from core.security import DEFAULT_SENSITIVE_KEYS


@dataclass(frozen=True, slots=True)
class MessageCatalog:
    locale: str
    owner_module: str
    messages: dict[str, str]
    excluded_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "excluded_codes", tuple(self.excluded_codes))
        if not self.locale.strip():
            raise AppError("VALIDATION_ERROR", "message locale is required", status_code=400)
        if not self.owner_module.strip():
            raise AppError("VALIDATION_ERROR", "message owner_module is required", status_code=400)
        if not self.messages:
            raise AppError("VALIDATION_ERROR", "message catalog must not be empty", status_code=400)
        for code, message in self.messages.items():
            if not code.strip():
                raise AppError("VALIDATION_ERROR", "message code is required", status_code=400)
            if not message.strip():
                raise AppError("VALIDATION_ERROR", "message text is required", status_code=400)
            if _contains_sensitive_word(message):
                raise AppError(
                    "VALIDATION_ERROR",
                    "message text must not contain sensitive words",
                    status_code=400,
                    details={"code": code, "reason": "sensitive_message"},
                )
        for code in self.excluded_codes:
            if not code.strip():
                raise AppError(
                    "VALIDATION_ERROR",
                    "excluded message code is required",
                    status_code=400,
                )


@dataclass(frozen=True, slots=True)
class ModuleMessageCatalog:
    locale: str
    messages: dict[str, str]
    excluded_codes: Sequence[str] = ()


def define_module_message_catalogs(
    owner_module: str,
    *,
    error_codes: Sequence[ErrorCodeSpec],
    catalogs: Sequence[ModuleMessageCatalog],
) -> list[MessageCatalog]:
    owner = owner_module.strip()
    if not owner:
        raise ValueError("owner_module is required")
    specs_by_code = {spec.code: spec for spec in error_codes}
    result: list[MessageCatalog] = []
    for catalog in catalogs:
        if not isinstance(catalog, ModuleMessageCatalog):
            raise TypeError("module message catalog must be ModuleMessageCatalog")
        excluded_codes = tuple(catalog.excluded_codes)
        _validate_catalog_coverage(
            owner_module=owner,
            locale=catalog.locale,
            specs_by_code=specs_by_code,
            messages=catalog.messages,
            excluded_codes=excluded_codes,
        )
        result.append(
            MessageCatalog(
                locale=catalog.locale,
                owner_module=owner,
                messages=dict(catalog.messages),
                excluded_codes=excluded_codes,
            )
        )
    return result


def _validate_catalog_coverage(
    *,
    owner_module: str,
    locale: str,
    specs_by_code: dict[str, ErrorCodeSpec],
    messages: dict[str, str],
    excluded_codes: tuple[str, ...],
) -> None:
    for spec in specs_by_code.values():
        if spec.owner_module != owner_module:
            raise ValueError(
                f"Error code {spec.code} owner_module must match module {owner_module!r}"
            )
    unknown_messages = sorted(set(messages).difference(specs_by_code))
    if unknown_messages:
        raise ValueError(
            f"Message catalog {locale} references undeclared codes: "
            f"{', '.join(unknown_messages)}"
        )
    unknown_exclusions = sorted(set(excluded_codes).difference(specs_by_code))
    if unknown_exclusions:
        raise ValueError(
            f"Message catalog {locale} excludes undeclared codes: "
            f"{', '.join(unknown_exclusions)}"
        )
    duplicated = sorted(set(messages).intersection(excluded_codes))
    if duplicated:
        raise ValueError(
            f"Message catalog {locale} defines and excludes codes: {', '.join(duplicated)}"
        )
    deprecated_messages = sorted(
        code for code in messages if specs_by_code[code].deprecated
    )
    if deprecated_messages:
        raise ValueError(
            f"Message catalog {locale} targets deprecated codes: "
            f"{', '.join(deprecated_messages)}"
        )
    missing = sorted(
        code
        for code, spec in specs_by_code.items()
        if not spec.deprecated and code not in messages and code not in excluded_codes
    )
    if missing:
        raise ValueError(
            f"Message catalog {locale} missing messages for codes: {', '.join(missing)}; "
            "add messages or excluded_codes"
        )


def _contains_sensitive_word(message: str) -> bool:
    lowered = message.lower()
    return any(word in lowered for word in DEFAULT_SENSITIVE_KEYS)
