from core.config.settings import Settings
from core.operations.dependencies import (
    DependencyProbeOutcome,
    check_profile_dependencies,
)


def test_cloud_dependency_checkpoint_requires_real_queue_redis_and_external_targets() -> None:
    result = check_profile_dependencies("cloud", _cloud_settings())

    payload = result.to_dict()

    assert result.ok is True
    assert payload["mode"] == "configuration"
    assert set(payload["probes"]) == {
        "database",
        "task_queue",
        "redis",
        "object_storage",
        "oidc",
    }
    assert payload["probes"]["task_queue"]["provider"] == "database"
    assert payload["probes"]["task_queue"]["status"] == "configured"
    assert payload["probes"]["redis"]["kind"] == "redis_tcp"
    assert payload["probes"]["redis"]["target"] == "rediss://:***@redis.example.com:6379/0"
    assert payload["probes"]["object_storage"]["kind"] == "http"
    assert payload["probes"]["oidc"]["kind"] == "http"
    assert payload["errors"] == []


def test_cloud_dependency_checkpoint_rejects_sync_queue_or_missing_redis() -> None:
    settings = _cloud_settings(
        task_queue={"provider": "sync"},
        dependencies={
            "object_storage_endpoint": "https://s3.example.com",
            "oidc_issuer_url": "https://auth.example.com/oidc",
        },
    )

    result = check_profile_dependencies("cloud", settings)

    assert result.ok is False
    assert result.to_dict()["probes"]["task_queue"]["status"] == "failed"
    assert result.to_dict()["probes"]["redis"]["status"] == "missing"
    assert result.errors == [
        "task_queue: cloud profile requires non-sync task queue provider",
        "redis: required dependency target is not configured",
    ]


def test_dependency_checkpoint_actual_probe_runner_controls_status() -> None:
    def probe_runner(name: str, target: str) -> DependencyProbeOutcome:
        if name == "redis":
            return DependencyProbeOutcome(ok=False, error="connection refused")
        return DependencyProbeOutcome(ok=True)

    result = check_profile_dependencies(
        "cloud",
        _cloud_settings(),
        run_actual=True,
        probe_runner=probe_runner,
    )

    payload = result.to_dict()

    assert result.ok is False
    assert payload["mode"] == "actual-probes"
    assert payload["probes"]["database"]["status"] == "passed"
    assert payload["probes"]["redis"]["status"] == "failed"
    assert payload["probes"]["redis"]["error"] == "connection refused"
    assert result.errors == ["redis: connection refused"]


def _cloud_settings(
    *,
    task_queue: dict[str, object] | None = None,
    dependencies: dict[str, object] | None = None,
) -> Settings:
    return Settings(
        app={"env": "cloud"},
        database={
            "url": "postgresql+asyncpg://app:secret@db.example.com:5432/fastapi_foundation",
            "tenant_fallback_mode": "session_variable",
            "tenant_fallback_setting_name": "app.tenant_id",
        },
        security={
            "jwt_secret_ref": "APP_JWT_SECRET",
            "cors_origins": ["https://console.example.com"],
            "trusted_hosts": ["api.example.com"],
        },
        task_queue=task_queue or {"provider": "database"},
        dependencies=dependencies
        or {
            "redis_url": "rediss://:secret@redis.example.com:6379/0",
            "object_storage_endpoint": "https://s3.example.com",
            "oidc_issuer_url": "https://auth.example.com/oidc",
        },
    )
