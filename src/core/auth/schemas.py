from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: str
    session_id: str
    auth_provider: str
    token_version: int
    tenant_id: str | None = None


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: str
    email: str
    display_name: str
    auth_provider: str
    session_id: str
    token_version: int
    tenant_id: str | None = None


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    user_id: str
    email: str
    display_name: str
    auth_provider: str
    session_id: str
    session_status: str
    user_status: str
    session_token_version: int
    user_token_version: int
    tenant_id: str | None = None
