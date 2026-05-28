from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.auth.errors import invalid_auth_token
from core.auth.schemas import TokenClaims


@dataclass(frozen=True, slots=True)
class LocalJwtConfig:
    secret: str
    issuer: str = "service-core"
    audience: str = "service-api"
    expires_in_seconds: int = 3600

    def __post_init__(self) -> None:
        if not self.secret:
            invalid_auth_token("missing_jwt_secret")
        if self.expires_in_seconds <= 0:
            invalid_auth_token("invalid_token_ttl")


class LocalJwtProvider:
    def __init__(self, config: LocalJwtConfig) -> None:
        self.config = config

    def issue_token(self, claims: TokenClaims, *, now: datetime | None = None) -> str:
        resolved_now = _timestamp(now or datetime.now(UTC))
        expires_at = resolved_now + self.config.expires_in_seconds
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "iss": self.config.issuer,
            "aud": self.config.audience,
            "iat": resolved_now,
            "exp": expires_at,
            "sub": claims.user_id,
            "sid": claims.session_id,
            "provider": claims.auth_provider,
            "ver": claims.token_version,
            "tid": claims.tenant_id,
        }
        signing_input = f"{_encode_json(header)}.{_encode_json(payload)}"
        return f"{signing_input}.{self._signature(signing_input)}"

    def verify_token(self, token: str, *, now: datetime | None = None) -> TokenClaims:
        header, payload, signing_input, signature = _decode_token(token)
        if header.get("alg") != "HS256":
            invalid_auth_token("unsupported_alg")
        if not hmac.compare_digest(self._signature(signing_input), signature):
            invalid_auth_token("invalid_signature")
        if payload.get("iss") != self.config.issuer:
            invalid_auth_token("issuer_mismatch")
        if payload.get("aud") != self.config.audience:
            invalid_auth_token("audience_mismatch")
        expires_at = _int_claim(payload, "exp")
        if expires_at <= _timestamp(now or datetime.now(UTC)):
            invalid_auth_token("token_expired")
        return TokenClaims(
            user_id=_str_claim(payload, "sub"),
            session_id=_str_claim(payload, "sid"),
            auth_provider=_str_claim(payload, "provider"),
            token_version=_int_claim(payload, "ver"),
            tenant_id=_optional_str_claim(payload, "tid"),
        )

    def _signature(self, signing_input: str) -> str:
        digest = hmac.new(
            self.config.secret.encode("utf-8"),
            signing_input.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return _base64url_encode(digest)


def _decode_token(token: str) -> tuple[dict[str, Any], dict[str, Any], str, str]:
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


def _encode_json(payload: dict[str, Any]) -> str:
    return _base64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _timestamp(value: datetime) -> int:
    resolved = value if value.tzinfo else value.replace(tzinfo=UTC)
    return int(resolved.timestamp())


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
