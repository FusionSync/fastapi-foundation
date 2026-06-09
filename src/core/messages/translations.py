from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from core.context.context import get_current_context
from core.exceptions.base import AppError
from core.security import DEFAULT_SENSITIVE_KEYS

DEFAULT_TRANSLATION_DOMAIN = "default"


@dataclass(frozen=True, slots=True)
class TranslationCatalog:
    locale: str
    domain: str
    owner_module: str
    messages: dict[str, str]

    def __post_init__(self) -> None:
        if not self.locale.strip():
            raise AppError("VALIDATION_ERROR", "translation locale is required", status_code=400)
        if not self.domain.strip():
            raise AppError("VALIDATION_ERROR", "translation domain is required", status_code=400)
        if not self.owner_module.strip():
            raise AppError(
                "VALIDATION_ERROR",
                "translation owner_module is required",
                status_code=400,
            )
        if not self.messages:
            raise AppError(
                "VALIDATION_ERROR",
                "translation catalog must not be empty",
                status_code=400,
            )
        for source, message in self.messages.items():
            if not source.strip():
                raise AppError(
                    "VALIDATION_ERROR",
                    "translation source is required",
                    status_code=400,
                )
            if not message.strip():
                raise AppError("VALIDATION_ERROR", "translation text is required", status_code=400)
            if _contains_sensitive_word(source) or _contains_sensitive_word(message):
                raise AppError(
                    "VALIDATION_ERROR",
                    "translation text must not contain sensitive words",
                    status_code=400,
                    details={"source": source, "reason": "sensitive_translation"},
                )


@dataclass(frozen=True, slots=True)
class ModuleTranslationCatalog:
    locale: str
    messages: dict[str, str]
    domain: str | None = None


class TranslationRegistry:
    def __init__(self) -> None:
        self._catalogs: dict[tuple[str, str], dict[str, str]] = {}

    def register(self, catalog: TranslationCatalog, *, replace: bool = False) -> None:
        key = (catalog.locale, catalog.domain)
        domain_messages = self._catalogs.setdefault(key, {})
        duplicates = sorted(
            source
            for source in set(domain_messages).intersection(catalog.messages)
            if domain_messages[source] != catalog.messages[source]
        )
        if duplicates and not replace:
            raise AppError(
                "VALIDATION_ERROR",
                "duplicate translation source in locale and domain",
                status_code=400,
                details={
                    "locale": catalog.locale,
                    "domain": catalog.domain,
                    "sources": duplicates,
                },
            )
        domain_messages.update(catalog.messages)

    def translate(
        self,
        source: str,
        *,
        locale: str | None = None,
        domain: str = DEFAULT_TRANSLATION_DOMAIN,
        params: Mapping[str, object] | None = None,
    ) -> str:
        resolved_locale = locale or _current_locale()
        template = self._resolve_template(source, locale=resolved_locale, domain=domain)
        return _format_translation(template, params=params)

    def catalogs(self) -> dict[tuple[str, str], dict[str, str]]:
        return {
            (locale, domain): dict(messages)
            for (locale, domain), messages in self._catalogs.items()
        }

    def _resolve_template(self, source: str, *, locale: str | None, domain: str) -> str:
        if locale is None:
            return source
        for candidate_locale in _locale_fallbacks(locale, self._domain_locales(domain)):
            messages = self._catalogs.get((candidate_locale, domain), {})
            if source in messages:
                return messages[source]
        return source

    def _domain_locales(self, domain: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                locale
                for locale, catalog_domain in self._catalogs
                if catalog_domain == domain
            )
        )


def define_module_translation_catalogs(
    owner_module: str,
    *,
    catalogs: Sequence[ModuleTranslationCatalog],
    domain: str | None = None,
) -> list[TranslationCatalog]:
    owner = owner_module.strip()
    if not owner:
        raise ValueError("owner_module is required")
    default_domain = domain or owner
    result: list[TranslationCatalog] = []
    for catalog in catalogs:
        if not isinstance(catalog, ModuleTranslationCatalog):
            raise TypeError("module translation catalog must be ModuleTranslationCatalog")
        result.append(
            TranslationCatalog(
                locale=catalog.locale,
                domain=catalog.domain or default_domain,
                owner_module=owner,
                messages=dict(catalog.messages),
            )
        )
    return result


_DEFAULT_TRANSLATION_REGISTRY = TranslationRegistry()


def register_translation_catalogs(
    *catalogs: TranslationCatalog,
    replace: bool = False,
) -> None:
    for catalog in catalogs:
        _DEFAULT_TRANSLATION_REGISTRY.register(catalog, replace=replace)


def translate(
    source: str,
    *,
    locale: str | None = None,
    domain: str = DEFAULT_TRANSLATION_DOMAIN,
    params: Mapping[str, object] | None = None,
) -> str:
    return _DEFAULT_TRANSLATION_REGISTRY.translate(
        source,
        locale=locale,
        domain=domain,
        params=params,
    )


def gettext(
    source: str,
    *,
    locale: str | None = None,
    domain: str = DEFAULT_TRANSLATION_DOMAIN,
    params: Mapping[str, object] | None = None,
) -> str:
    return translate(source, locale=locale, domain=domain, params=params)


def iter_translation_catalogs() -> tuple[tuple[str, str, dict[str, str]], ...]:
    return tuple(
        (locale, domain, messages)
        for (locale, domain), messages in sorted(_DEFAULT_TRANSLATION_REGISTRY.catalogs().items())
    )


def _current_locale() -> str | None:
    context = get_current_context()
    return context.locale if context else None


def _locale_fallbacks(locale: str, registered_locales: tuple[str, ...]) -> tuple[str, ...]:
    candidates = [locale]
    language = locale.split("-", 1)[0]
    for registered_locale in registered_locales:
        if registered_locale == locale:
            continue
        if registered_locale.split("-", 1)[0] == language:
            candidates.append(registered_locale)
    return tuple(candidates)


def _format_translation(
    template: str,
    *,
    params: Mapping[str, object] | None,
) -> str:
    if not params:
        return template
    try:
        return template.format(**params)
    except KeyError as exc:
        raise AppError(
            "VALIDATION_ERROR",
            "translation params are missing required keys",
            status_code=400,
            details={"missing_key": str(exc.args[0])},
        ) from exc


def _contains_sensitive_word(message: str) -> bool:
    lowered = message.lower()
    return any(word in lowered for word in DEFAULT_SENSITIVE_KEYS)
