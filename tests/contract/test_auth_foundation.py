import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from core.auth import (
    AuthSessionValidator,
    ExternalAuthCallback,
    HmacOidcIdTokenVerifier,
    LocalJwtConfig,
    LocalJwtProvider,
    MemoryExternalAuthStateStore,
    OidcProviderAdapter,
    OidcProviderConfig,
    OidcTokenSet,
    SessionPrincipal,
    StaticAuthSessionStore,
    TokenClaims,
    keycloak_oidc_provider_config,
    logto_oidc_provider_config,
)
from core.exceptions import AppError
from core.security import PasswordHasher


def test_password_hasher_hashes_and_verifies_without_storing_plaintext() -> None:
    hasher = PasswordHasher(iterations=1000, salt="fixed-salt")

    password_hash = hasher.hash_password("CorrectHorse1")

    assert password_hash != "CorrectHorse1"
    assert password_hash.startswith("pbkdf2_sha256$1000$")
    assert hasher.verify_password("CorrectHorse1", password_hash) is True
    assert hasher.verify_password("wrong-password", password_hash) is False
    assert hasher.verify_password("CorrectHorse1", "not-a-valid-hash") is False


def test_password_hasher_rejects_short_passwords() -> None:
    with pytest.raises(AppError) as rejected:
        PasswordHasher(min_length=8).hash_password("short")

    assert rejected.value.code == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_local_jwt_provider_issues_verifies_and_authenticates_claims() -> None:
    now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
    provider = LocalJwtProvider(
        LocalJwtConfig(
            secret="test-secret",
            issuer="core-test",
            audience="api",
            expires_in_seconds=300,
        )
    )
    claims = TokenClaims(
        user_id="user-1",
        session_id="sess-1",
        auth_provider="local",
        token_version=2,
        tenant_id="tenant-a",
    )

    token = provider.issue_token(claims, now=now)
    verified_claims = provider.verify_token(token, now=now + timedelta(seconds=30))
    current_user = await AuthSessionValidator(
        StaticAuthSessionStore(
            {
                "sess-1": SessionPrincipal(
                    user_id="user-1",
                    email="owner@example.com",
                    display_name="Owner",
                    auth_provider="local",
                    session_id="sess-1",
                    session_status="active",
                    user_status="active",
                    session_token_version=2,
                    user_token_version=2,
                    tenant_id="tenant-a",
                )
            }
        )
    ).authenticate(verified_claims)

    assert token.count(".") == 2
    assert verified_claims == claims
    assert current_user.id == "user-1"


def test_local_jwt_provider_rejects_expired_or_tampered_tokens() -> None:
    now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
    provider = LocalJwtProvider(
        LocalJwtConfig(secret="test-secret", issuer="core-test", audience="api")
    )
    token = provider.issue_token(
        TokenClaims(
            user_id="user-1",
            session_id="sess-1",
            auth_provider="local",
            token_version=1,
            tenant_id="tenant-a",
        ),
        now=now,
    )

    with pytest.raises(AppError) as expired:
        provider.verify_token(token, now=now + timedelta(hours=2))

    parts = token.split(".")
    tampered = ".".join([parts[0], parts[1], "bad-signature"])
    with pytest.raises(AppError) as invalid_signature:
        provider.verify_token(tampered, now=now)

    assert expired.value.details == {"reason": "token_expired"}
    assert invalid_signature.value.details == {"reason": "invalid_signature"}


@pytest.mark.parametrize(
    ("verifier_config", "reason"),
    [
        (
            LocalJwtConfig(secret="test-secret", issuer="issuer-b", audience="api-a"),
            "issuer_mismatch",
        ),
        (
            LocalJwtConfig(secret="test-secret", issuer="issuer-a", audience="api-b"),
            "audience_mismatch",
        ),
    ],
)
def test_local_jwt_provider_rejects_wrong_issuer_or_audience(
    verifier_config: LocalJwtConfig, reason: str
) -> None:
    now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
    issuer = LocalJwtProvider(
        LocalJwtConfig(secret="test-secret", issuer="issuer-a", audience="api-a")
    )
    verifier = LocalJwtProvider(verifier_config)
    token = issuer.issue_token(
        TokenClaims(
            user_id="user-1",
            session_id="sess-1",
            auth_provider="local",
            token_version=1,
            tenant_id=None,
        ),
        now=now,
    )

    with pytest.raises(AppError) as rejected:
        verifier.verify_token(token, now=now)

    assert rejected.value.details == {"reason": reason}


@pytest.mark.asyncio
async def test_auth_session_validator_returns_current_user_for_active_session() -> None:
    validator = AuthSessionValidator(
        StaticAuthSessionStore(
            {
                "sess-1": SessionPrincipal(
                    user_id="user-1",
                    email="owner@example.com",
                    display_name="Owner",
                    auth_provider="local",
                    session_id="sess-1",
                    session_status="active",
                    user_status="active",
                    session_token_version=2,
                    user_token_version=2,
                    tenant_id="tenant-a",
                )
            }
        )
    )

    current_user = await validator.authenticate(
        TokenClaims(
            user_id="user-1",
            session_id="sess-1",
            auth_provider="local",
            token_version=2,
            tenant_id="tenant-a",
        )
    )

    assert current_user.id == "user-1"
    assert current_user.email == "owner@example.com"
    assert current_user.display_name == "Owner"
    assert current_user.auth_provider == "local"
    assert current_user.session_id == "sess-1"
    assert current_user.token_version == 2
    assert current_user.tenant_id == "tenant-a"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("principal_overrides", "claims_overrides", "reason"),
    [
        ({"session_status": "revoked"}, {}, "session_not_active"),
        ({"user_status": "disabled"}, {}, "user_not_active"),
        ({}, {"token_version": 1}, "token_version_mismatch"),
        ({}, {"tenant_id": "tenant-b"}, "tenant_mismatch"),
        ({"session_token_version": 1}, {}, "session_token_version_mismatch"),
    ],
)
async def test_auth_session_validator_rejects_revoked_or_stale_facts(
    principal_overrides: dict[str, object],
    claims_overrides: dict[str, object],
    reason: str,
) -> None:
    principal_values = {
        "user_id": "user-1",
        "email": "owner@example.com",
        "display_name": "Owner",
        "auth_provider": "local",
        "session_id": "sess-1",
        "session_status": "active",
        "user_status": "active",
        "session_token_version": 2,
        "user_token_version": 2,
        "tenant_id": "tenant-a",
        **principal_overrides,
    }
    claims_values = {
        "user_id": "user-1",
        "session_id": "sess-1",
        "auth_provider": "local",
        "token_version": 2,
        "tenant_id": "tenant-a",
        **claims_overrides,
    }
    validator = AuthSessionValidator(
        StaticAuthSessionStore({"sess-1": SessionPrincipal(**principal_values)})
    )

    with pytest.raises(AppError) as rejected:
        await validator.authenticate(TokenClaims(**claims_values))

    assert rejected.value.code == "AUTH_INVALID_TOKEN"
    assert rejected.value.status_code == 401
    assert rejected.value.headers == {"WWW-Authenticate": "Bearer"}
    assert rejected.value.details == {"reason": reason}


@pytest.mark.asyncio
async def test_oidc_provider_adapter_builds_authorization_url_and_handles_callback() -> None:
    now = datetime(2026, 5, 29, 10, 0, tzinfo=UTC)
    config = OidcProviderConfig(
        provider="logto",
        issuer="https://auth.example.com/oidc",
        client_id="web-app",
        client_secret="oidc-secret",
        redirect_uri="https://api.example.com/api/v1/auth/external/logto/callback",
        authorization_endpoint="https://auth.example.com/oidc/auth",
        token_endpoint="https://auth.example.com/oidc/token",
        scopes=("openid", "profile", "email", "urn:logto:scope:organizations"),
        tenant_claim="organization_id",
    )
    state_store = MemoryExternalAuthStateStore(ttl_seconds=300)
    state = state_store.issue(
        provider="logto",
        tenant_id="tenant-a",
        redirect_after="/console",
        now=now,
    )
    adapter = OidcProviderAdapter(config)

    authorization_url = adapter.authorization_url(state)
    parsed = urlparse(authorization_url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.example.com"
    assert parsed.path == "/oidc/auth"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["web-app"]
    assert query["redirect_uri"] == [config.redirect_uri]
    assert query["scope"] == ["openid profile email urn:logto:scope:organizations"]
    assert query["state"] == [state.state]
    assert query["nonce"] == [state.nonce]

    token = _signed_oidc_token(
        {
            "iss": config.issuer,
            "aud": config.client_id,
            "sub": "logto-user-1",
            "email": "User@Example.com",
            "name": "Logto User",
            "nonce": state.nonce,
            "organization_id": "tenant-a",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        secret="oidc-secret",
    )
    identity = await adapter.handle_callback(
        ExternalAuthCallback(code="auth-code", state=state.state),
        state=state_store.consume(provider="logto", state=state.state, now=now),
        client=_FakeOidcClient(token),
        verifier=HmacOidcIdTokenVerifier("oidc-secret"),
        now=now,
    )

    assert identity.provider == "logto"
    assert identity.subject == "logto-user-1"
    assert identity.email == "user@example.com"
    assert identity.display_name == "Logto User"
    assert identity.tenant_id == "tenant-a"
    assert identity.token_set.access_token == "external-access-token"


def test_oidc_provider_callback_rejects_state_nonce_or_signature_mismatch() -> None:
    now = datetime(2026, 5, 29, 10, 0, tzinfo=UTC)
    config = OidcProviderConfig(
        provider="keycloak",
        issuer="https://sso.internal.example/realms/main",
        client_id="api",
        client_secret="oidc-secret",
        redirect_uri="https://api.internal.example/api/v1/auth/external/keycloak/callback",
        authorization_endpoint="https://sso.internal.example/realms/main/protocol/openid-connect/auth",
        token_endpoint="https://sso.internal.example/realms/main/protocol/openid-connect/token",
    )
    state_store = MemoryExternalAuthStateStore(ttl_seconds=300)
    state = state_store.issue(provider="keycloak", now=now)
    token = _signed_oidc_token(
        {
            "iss": config.issuer,
            "aud": config.client_id,
            "sub": "keycloak-user-1",
            "email": "user@example.com",
            "nonce": "wrong-nonce",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        secret="oidc-secret",
    )

    with pytest.raises(AppError) as wrong_state:
        state_store.consume(provider="keycloak", state="wrong-state", now=now)
    with pytest.raises(AppError) as wrong_nonce:
        HmacOidcIdTokenVerifier("oidc-secret").verify(
            config,
            token,
            expected_nonce=state.nonce,
            now=now,
        )
    with pytest.raises(AppError) as wrong_signature:
        HmacOidcIdTokenVerifier("different-secret").verify(
            config,
            token,
            expected_nonce="wrong-nonce",
            now=now,
        )

    assert wrong_state.value.details == {"reason": "external_auth_state_not_found"}
    assert wrong_nonce.value.details == {"reason": "nonce_mismatch"}
    assert wrong_signature.value.details == {"reason": "invalid_signature"}


def test_logto_and_keycloak_oidc_provider_configs_set_provider_defaults() -> None:
    logto = logto_oidc_provider_config(
        issuer="https://auth.example.com/oidc",
        client_id="logto-client",
        client_secret="logto-secret",
        redirect_uri="https://api.example.com/auth/external/logto/callback",
    )
    keycloak = keycloak_oidc_provider_config(
        realm_url="https://sso.internal.example/realms/main",
        client_id="keycloak-client",
        client_secret="keycloak-secret",
        redirect_uri="https://api.internal.example/auth/external/keycloak/callback",
    )

    assert logto.provider == "logto"
    assert logto.authorization_endpoint == "https://auth.example.com/oidc/auth"
    assert logto.token_endpoint == "https://auth.example.com/oidc/token"
    assert "urn:logto:scope:organizations" in logto.scopes
    assert logto.tenant_claim == "organization_id"

    assert keycloak.provider == "keycloak"
    assert keycloak.issuer == "https://sso.internal.example/realms/main"
    assert keycloak.authorization_endpoint.endswith("/protocol/openid-connect/auth")
    assert keycloak.token_endpoint.endswith("/protocol/openid-connect/token")


class _FakeOidcClient:
    def __init__(self, id_token: str) -> None:
        self.id_token = id_token

    async def exchange_authorization_code(
        self,
        config: OidcProviderConfig,
        *,
        code: str,
    ) -> OidcTokenSet:
        assert config.token_endpoint.endswith("/token")
        assert code == "auth-code"
        return OidcTokenSet(
            id_token=self.id_token,
            access_token="external-access-token",
            token_type="Bearer",
            expires_in=300,
        )


def _signed_oidc_token(payload: dict[str, object], *, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_encode_json(header)}.{_encode_json(payload)}"
    signature = hmac.new(
        secret.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_base64url_encode(signature)}"


def _encode_json(payload: dict[str, object]) -> str:
    return _base64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
