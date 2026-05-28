from __future__ import annotations

from typing import Protocol

from core.auth.errors import invalid_auth_token
from core.auth.schemas import CurrentUser, SessionPrincipal, TokenClaims


class AuthSessionStore(Protocol):
    async def load_principal(self, session_id: str) -> SessionPrincipal | None: ...


class StaticAuthSessionStore(AuthSessionStore):
    def __init__(self, principals: dict[str, SessionPrincipal]) -> None:
        self.principals = principals

    async def load_principal(self, session_id: str) -> SessionPrincipal | None:
        return self.principals.get(session_id)


class AuthSessionValidator:
    def __init__(self, store: AuthSessionStore) -> None:
        self.store = store

    async def authenticate(self, claims: TokenClaims) -> CurrentUser:
        self._validate_claims(claims)
        principal = await self.store.load_principal(claims.session_id)
        if principal is None:
            invalid_auth_token("session_not_found")
        if principal.user_id != claims.user_id:
            invalid_auth_token("user_mismatch")
        if principal.auth_provider != claims.auth_provider:
            invalid_auth_token("provider_mismatch")
        if principal.session_status != "active":
            invalid_auth_token("session_not_active")
        if principal.user_status != "active":
            invalid_auth_token("user_not_active")
        if principal.user_token_version != claims.token_version:
            invalid_auth_token("token_version_mismatch")
        if principal.session_token_version != claims.token_version:
            invalid_auth_token("session_token_version_mismatch")
        if principal.tenant_id != claims.tenant_id:
            invalid_auth_token("tenant_mismatch")
        return CurrentUser(
            id=principal.user_id,
            external_id=principal.external_id,
            email=principal.email,
            display_name=principal.display_name,
            auth_provider=principal.auth_provider,
            session_id=principal.session_id,
            token_version=principal.user_token_version,
            tenant_id=principal.tenant_id,
        )

    def _validate_claims(self, claims: TokenClaims) -> None:
        required = {
            "user_id": claims.user_id,
            "session_id": claims.session_id,
            "auth_provider": claims.auth_provider,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            invalid_auth_token(f"missing_{missing[0]}")
        if claims.token_version < 1:
            invalid_auth_token("invalid_token_version")
