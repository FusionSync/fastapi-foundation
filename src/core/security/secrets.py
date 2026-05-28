from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from core.config.settings import Settings


class SecretProvider(Protocol):
    def get_secret(self, name: str) -> str | None:
        """Return the secret value for a configured reference."""


@dataclass(frozen=True, slots=True)
class EnvSecretProvider:
    prefix: str = ""

    def get_secret(self, name: str) -> str | None:
        return os.environ.get(f"{self.prefix}{name}")


@dataclass(frozen=True, slots=True)
class MappingSecretProvider:
    values: dict[str, str]

    def get_secret(self, name: str) -> str | None:
        return self.values.get(name)


def resolve_settings_secrets(
    settings: Settings,
    provider: SecretProvider | None,
) -> Settings:
    if provider is None or settings.security.jwt_secret_ref is None:
        return settings

    resolved = provider.get_secret(settings.security.jwt_secret_ref)
    if resolved is None:
        raise ValueError(f"Secret ref {settings.security.jwt_secret_ref!r} was not found")
    if settings.security.jwt_secret != "change-me":
        return settings
    return settings.model_copy(
        deep=True,
        update={
            "security": settings.security.model_copy(update={"jwt_secret": resolved}),
        },
    )
