import pytest

from core.config.settings import DatabaseSettings, Settings
from core.db import DatabaseTenantFallback, create_database_runtime
from core.exceptions import AppError


@pytest.mark.asyncio
async def test_database_runtime_exposes_read_write_split_and_pool_diagnostics() -> None:
    runtime = create_database_runtime(
        Settings(
            database=DatabaseSettings(
                url="sqlite+aiosqlite:///./data/write.db",
                read_url="sqlite+aiosqlite:///./data/read.db",
                pool_size=5,
                max_overflow=7,
            )
        )
    )
    try:
        diagnostics = runtime.diagnostics().to_dict()

        assert runtime.session_factory_for("write") is runtime.session_factory
        assert runtime.session_factory_for("read") is runtime.read_session_factory
        assert diagnostics["write"]["url"] == "sqlite+aiosqlite:///./data/write.db"
        assert diagnostics["write"]["pool"]["class"]
        assert diagnostics["read"] == {
            "configured": True,
            "url": "sqlite+aiosqlite:///./data/read.db",
            "pool": diagnostics["read"]["pool"],
        }
        assert diagnostics["read"]["pool"]["class"]
        assert diagnostics["tenant_fallback"] == {
            "mode": "disabled",
            "setting_name": "app.tenant_id",
        }
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_database_runtime_unit_of_work_applies_tenant_fallback_to_session_info() -> None:
    runtime = create_database_runtime(
        Settings(
            database=DatabaseSettings(
                url="sqlite+aiosqlite:///:memory:",
                tenant_fallback_mode="session_variable",
                tenant_fallback_setting_name="app.tenant_id",
            )
        )
    )
    try:
        async with runtime.unit_of_work(tenant_id="tenant-a") as uow:
            assert uow.tenant_id == "tenant-a"
            assert uow.session is not None
            assert uow.session.info["tenant_id"] == "tenant-a"
            assert uow.session.info["tenant_fallback"] == {
                "mode": "session_variable",
                "setting_name": "app.tenant_id",
            }
    finally:
        await runtime.dispose()


@pytest.mark.asyncio
async def test_database_tenant_fallback_rejects_missing_tenant_id() -> None:
    runtime = create_database_runtime(Settings(database=DatabaseSettings()))
    try:
        async with runtime.session_factory() as session:
            with pytest.raises(AppError) as rejected:
                await DatabaseTenantFallback(mode="session_variable").apply(session, "")

        assert rejected.value.code == "VALIDATION_ERROR"
    finally:
        await runtime.dispose()
