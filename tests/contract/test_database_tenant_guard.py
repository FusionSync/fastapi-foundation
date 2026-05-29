from core.config.settings import Settings
from core.db import verify_database_tenant_guard
from core.operations import check_config


def test_cloud_postgresql_tenant_guard_reports_rls_and_advisory_strategy() -> None:
    report = verify_database_tenant_guard(
        Settings(
            app={"env": "cloud"},
            database={
                "url": "postgresql+asyncpg://app:secret@db.example.com:5432/fastapi_foundation",
                "tenant_fallback_mode": "session_variable",
                "tenant_fallback_setting_name": "app.tenant_id",
            },
        ),
        tenant_tables=["bid_records"],
    )

    payload = report.to_dict()

    assert report.ok is True
    assert report.required is True
    assert payload["dialect"] == "postgresql"
    assert payload["strategy"] == "repository_plus_rls"
    assert payload["checks"]["postgresql_dialect"] == "ok"
    assert payload["checks"]["session_variable"] == "ok"
    assert payload["checks"]["rls_policy_plan"] == "ok"
    assert payload["checks"]["advisory_lock_plan"] == "ok"
    assert payload["errors"] == []
    assert payload["rls_policies"] == [
        {
            "table": "bid_records",
            "policy_name": "bid_records_tenant_isolation",
            "enable_rls": "ALTER TABLE bid_records ENABLE ROW LEVEL SECURITY",
            "force_rls": "ALTER TABLE bid_records FORCE ROW LEVEL SECURITY",
            "using_expression": "tenant_id = current_setting('app.tenant_id', true)",
            "with_check_expression": "tenant_id = current_setting('app.tenant_id', true)",
            "create_policy": (
                "CREATE POLICY bid_records_tenant_isolation ON bid_records "
                "USING (tenant_id = current_setting('app.tenant_id', true)) "
                "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
            ),
        }
    ]
    assert payload["advisory_lock"] == {
        "scope": "transaction",
        "statement": (
            "SELECT pg_advisory_xact_lock("
            "hashtext(current_setting('app.tenant_id', true)))"
        ),
        "key_expression": "hashtext(current_setting('app.tenant_id', true))",
    }


def test_cloud_tenant_guard_rejects_missing_session_variable_fallback() -> None:
    report = verify_database_tenant_guard(
        Settings(
            app={"env": "cloud"},
            database={
                "url": "postgresql+asyncpg://app:secret@db.example.com:5432/fastapi_foundation",
                "tenant_fallback_mode": "disabled",
            },
        )
    )

    assert report.ok is False
    assert report.required is True
    assert report.checks["session_variable"] == "error"
    assert report.errors == [
        "cloud profile requires DATABASE__TENANT_FALLBACK_MODE=session_variable"
    ]


def test_cloud_config_check_includes_database_tenant_guard_evidence() -> None:
    result = check_config(
        "cloud",
        Settings(
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
        ),
    )

    assert result.ok is True
    assert result.errors == []
    assert result.details["database_tenant_guard"]["required"] is True
    assert result.details["database_tenant_guard"]["checks"]["session_variable"] == "ok"
