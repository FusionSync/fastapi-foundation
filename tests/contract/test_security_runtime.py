from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings
from core.security import EnvSecretProvider, MappingSecretProvider, resolve_settings_secrets


def test_app_factory_applies_security_runtime_middleware() -> None:
    settings = Settings(
        security={
            "cors_origins": ["https://console.example.com"],
            "trusted_hosts": ["api.example.com"],
            "max_request_body_bytes": 4,
        }
    )
    client = TestClient(create_app(settings), base_url="http://api.example.com")

    response = client.get("/healthz", headers={"Origin": "https://console.example.com"})

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "https://console.example.com"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"

    rejected = client.post("/healthz", content=b"12345")

    assert rejected.status_code == 413
    assert rejected.headers["X-App-Code"] == "REQUEST_TOO_LARGE"
    assert rejected.headers["X-Content-Type-Options"] == "nosniff"
    assert rejected.json()["code"] == "REQUEST_TOO_LARGE"
    assert rejected.json()["details"] == {"max_bytes": 4, "content_length": 5}


def test_app_factory_rejects_untrusted_host_with_envelope() -> None:
    settings = Settings(security={"trusted_hosts": ["api.example.com"]})
    client = TestClient(create_app(settings), base_url="http://evil.example.com")

    response = client.get("/healthz")

    assert response.status_code == 400
    assert response.headers["X-App-Code"] == "HOST_NOT_ALLOWED"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.json()["code"] == "HOST_NOT_ALLOWED"
    assert response.json()["details"] == {"host": "evil.example.com"}


def test_secret_provider_resolves_jwt_secret_ref_without_overriding_explicit_secret() -> None:
    settings = Settings(
        security={
            "jwt_secret": "explicit-secret",
            "jwt_secret_ref": "JWT_SECRET",
        }
    )

    resolved = resolve_settings_secrets(
        settings,
        MappingSecretProvider({"JWT_SECRET": "provider-secret"}),
    )

    assert resolved.security.jwt_secret == "explicit-secret"


def test_env_secret_provider_resolves_default_jwt_secret(monkeypatch) -> None:
    monkeypatch.setenv("APP_JWT_SECRET", "resolved-from-env")
    settings = Settings(security={"jwt_secret_ref": "APP_JWT_SECRET"})

    resolved = resolve_settings_secrets(settings, EnvSecretProvider())

    assert resolved.security.jwt_secret == "resolved-from-env"


def test_app_factory_validates_after_secret_resolution() -> None:
    settings = Settings(
        app={"env": "cloud"},
        security={
            "jwt_secret_ref": "JWT_SECRET",
            "trusted_hosts": ["testserver"],
        },
    )

    app = create_app(settings, secret_provider=MappingSecretProvider({"JWT_SECRET": "safe"}))

    assert app.state.settings.security.jwt_secret == "safe"


def test_missing_secret_ref_fails_before_startup() -> None:
    settings = Settings(security={"jwt_secret_ref": "MISSING"})

    try:
        create_app(settings, secret_provider=MappingSecretProvider({}))
    except ValueError as exc:
        payload = str(exc)
    else:
        raise AssertionError("missing secret ref accepted")

    assert "MISSING" in payload
