from datetime import UTC, datetime, timedelta

import pytest

from core.auth import (
    AuthSessionValidator,
    LocalJwtConfig,
    LocalJwtProvider,
    SessionPrincipal,
    StaticAuthSessionStore,
    TokenClaims,
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
                    external_id=None,
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
                    external_id="ext-1",
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
    assert current_user.external_id == "ext-1"
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
        "external_id": None,
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
