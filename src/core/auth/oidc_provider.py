from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Any, Protocol
from urllib.parse import urlencode

from core.auth.errors import invalid_auth_token
from core.exceptions import AppError


@dataclass(frozen=True, slots=True)
class OidcProviderConfig:
    provider: str
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    authorization_endpoint: str
    token_endpoint: str
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    tenant_claim: str = "tenant_id"
    authorization_extra_params: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        required = {
            "provider": self.provider,
            "issuer": self.issuer,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise AppError(
                "VALIDATION_ERROR",
                f"OIDC {missing[0]} is required",
                status_code=400,
            )
        if "openid" not in self.scopes:
            raise AppError(
                "VALIDATION_ERROR",
                "OIDC scopes must include openid",
                status_code=400,
            )


@dataclass(frozen=True, slots=True)
class ExternalAuthState:
    provider: str
    state: str
    nonce: str
    tenant_id: str | None
    redirect_after: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ExternalAuthCallback:
    code: str
    state: str


@dataclass(frozen=True, slots=True)
class OidcTokenSet:
    id_token: str
    access_token: str
    token_type: str
    expires_in: int | None = None
    refresh_token: str | None = None
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OidcUserClaims:
    subject: str
    email: str
    display_name: str
    email_verified: bool | None = None
    tenant_id: str | None = None
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExternalAuthIdentity:
    provider: str
    subject: str
    email: str
    display_name: str
    token_set: OidcTokenSet
    tenant_id: str | None = None
    raw_claims: dict[str, object] = field(default_factory=dict)


class ExternalAuthStateStore(Protocol):
    def issue(
        self,
        *,
        provider: str,
        tenant_id: str | None = None,
        redirect_after: str | None = None,
        now: datetime | None = None,
    ) -> ExternalAuthState: ...

    def consume(
        self,
        *,
        provider: str,
        state: str,
        now: datetime | None = None,
    ) -> ExternalAuthState: ...


class OidcClient(Protocol):
    async def exchange_authorization_code(
        self,
        config: OidcProviderConfig,
        *,
        code: str,
    ) -> OidcTokenSet: ...


class OidcIdTokenVerifier(Protocol):
    def verify(
        self,
        config: OidcProviderConfig,
        id_token: str,
        *,
        expected_nonce: str,
        now: datetime | None = None,
    ) -> OidcUserClaims: ...


class MemoryExternalAuthStateStore:
    def __init__(self, *, ttl_seconds: int = 300) -> None:
        if ttl_seconds <= 0:
            raise AppError("VALIDATION_ERROR", "external auth state ttl must be positive")
        self.ttl_seconds = ttl_seconds
        self._states: dict[str, ExternalAuthState] = {}

    def issue(
        self,
        *,
        provider: str,
        tenant_id: str | None = None,
        redirect_after: str | None = None,
        now: datetime | None = None,
    ) -> ExternalAuthState:
        if not provider.strip():
            raise AppError("VALIDATION_ERROR", "external auth provider is required")
        resolved_now = _aware_now(now)
        state = ExternalAuthState(
            provider=provider.strip(),
            state=token_urlsafe(32),
            nonce=token_urlsafe(32),
            tenant_id=tenant_id,
            redirect_after=redirect_after,
            expires_at=resolved_now + timedelta(seconds=self.ttl_seconds),
        )
        self._states[state.state] = state
        return state

    def consume(
        self,
        *,
        provider: str,
        state: str,
        now: datetime | None = None,
    ) -> ExternalAuthState:
        stored = self._states.pop(state, None)
        if stored is None:
            invalid_auth_token("external_auth_state_not_found")
        if stored.provider != provider:
            invalid_auth_token("provider_mismatch")
        if stored.expires_at <= _aware_now(now):
            invalid_auth_token("external_auth_state_expired")
        return stored


class OidcProviderAdapter:
    def __init__(self, config: OidcProviderConfig) -> None:
        self.config = config

    def authorization_url(self, state: ExternalAuthState) -> str:
        if state.provider != self.config.provider:
            invalid_auth_token("provider_mismatch")
        query = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scopes),
            "state": state.state,
            "nonce": state.nonce,
            **dict(self.config.authorization_extra_params),
        }
        return f"{self.config.authorization_endpoint}?{urlencode(query)}"

    async def handle_callback(
        self,
        callback: ExternalAuthCallback,
        *,
        state: ExternalAuthState,
        client: OidcClient,
        verifier: OidcIdTokenVerifier,
        now: datetime | None = None,
    ) -> ExternalAuthIdentity:
        if callback.state != state.state:
            invalid_auth_token("state_mismatch")
        if not callback.code.strip():
            invalid_auth_token("missing_authorization_code")
        token_set = await client.exchange_authorization_code(
            self.config,
            code=callback.code.strip(),
        )
        claims = verifier.verify(
            self.config,
            token_set.id_token,
            expected_nonce=state.nonce,
            now=now,
        )
        return ExternalAuthIdentity(
            provider=self.config.provider,
            subject=claims.subject,
            email=claims.email.strip().lower(),
            display_name=claims.display_name,
            tenant_id=claims.tenant_id or state.tenant_id,
            token_set=token_set,
            raw_claims=claims.raw,
        )


class HmacOidcIdTokenVerifier:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise AppError("VALIDATION_ERROR", "OIDC verification secret is required")
        self.secret = secret

    def verify(
        self,
        config: OidcProviderConfig,
        id_token: str,
        *,
        expected_nonce: str,
        now: datetime | None = None,
    ) -> OidcUserClaims:
        header, payload, signing_input, signature = _decode_jwt(id_token)
        if header.get("alg") != "HS256":
            invalid_auth_token("unsupported_alg")
        if not hmac.compare_digest(_signature(self.secret, signing_input), signature):
            invalid_auth_token("invalid_signature")
        if payload.get("iss") != config.issuer:
            invalid_auth_token("issuer_mismatch")
        if not _audience_matches(payload.get("aud"), config.client_id):
            invalid_auth_token("audience_mismatch")
        expires_at = _int_claim(payload, "exp")
        if expires_at <= int(_aware_now(now).timestamp()):
            invalid_auth_token("token_expired")
        nonce = _str_claim(payload, "nonce")
        if nonce != expected_nonce:
            invalid_auth_token("nonce_mismatch")
        subject = _str_claim(payload, "sub")
        email = _str_claim(payload, "email").strip().lower()
        display_name = _optional_str_claim(payload, "name") or email or subject
        tenant_id = _optional_str_claim(payload, config.tenant_claim)
        email_verified = payload.get("email_verified")
        if email_verified is not None and not isinstance(email_verified, bool):
            invalid_auth_token("invalid_email_verified")
        return OidcUserClaims(
            subject=subject,
            email=email,
            display_name=display_name,
            email_verified=email_verified,
            tenant_id=tenant_id,
            raw=dict(payload),
        )


def logto_oidc_provider_config(
    *,
    issuer: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> OidcProviderConfig:
    resolved_issuer = issuer.rstrip("/")
    return OidcProviderConfig(
        provider="logto",
        issuer=resolved_issuer,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        authorization_endpoint=f"{resolved_issuer}/auth",
        token_endpoint=f"{resolved_issuer}/token",
        scopes=("openid", "profile", "email", "urn:logto:scope:organizations"),
        tenant_claim="organization_id",
    )


def keycloak_oidc_provider_config(
    *,
    realm_url: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> OidcProviderConfig:
    resolved_realm_url = realm_url.rstrip("/")
    oidc_base = f"{resolved_realm_url}/protocol/openid-connect"
    return OidcProviderConfig(
        provider="keycloak",
        issuer=resolved_realm_url,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        authorization_endpoint=f"{oidc_base}/auth",
        token_endpoint=f"{oidc_base}/token",
        scopes=("openid", "profile", "email"),
        tenant_claim="tenant_id",
    )


def _decode_jwt(token: str) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    parts = token.split(".")
    if len(parts) != 3:
        invalid_auth_token("malformed_token")
    header_segment, payload_segment, signature = parts
    try:
        header = json.loads(_base64url_decode(header_segment))
        payload = json.loads(_base64url_decode(payload_segment))
    except (ValueError, json.JSONDecodeError):
        invalid_auth_token("malformed_token")
    if not isinstance(header, dict) or not isinstance(payload, dict):
        invalid_auth_token("malformed_token")
    return header, payload, f"{header_segment}.{payload_segment}", signature


def _signature(secret: str, signing_input: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _aware_now(value: datetime | None) -> datetime:
    resolved = value or datetime.now(UTC)
    return resolved if resolved.tzinfo else resolved.replace(tzinfo=UTC)


def _str_claim(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        invalid_auth_token(f"missing_{name}")
    return value


def _optional_str_claim(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        invalid_auth_token(f"invalid_{name}")
    return value


def _int_claim(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int):
        invalid_auth_token(f"missing_{name}")
    return value


def _audience_matches(raw_audience: object, expected: str) -> bool:
    if isinstance(raw_audience, str):
        return raw_audience == expected
    if isinstance(raw_audience, list):
        return expected in raw_audience
    return False
