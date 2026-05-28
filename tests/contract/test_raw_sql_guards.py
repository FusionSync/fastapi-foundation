from typing import Any

import pytest

from core.context import RequestContext, reset_current_context, set_current_context
from core.db.sql import execute_cross_tenant, execute_tenant_scoped
from core.exceptions import AppError


@pytest.mark.asyncio
async def test_tenant_scoped_sql_requires_tenant_context() -> None:
    with pytest.raises(AppError) as exc_info:
        await execute_tenant_scoped(
            _FakeSession(),  # type: ignore[arg-type]
            "select * from records where tenant_id = :tenant_id",
        )

    assert exc_info.value.code == "TENANT_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_tenant_scoped_sql_injects_current_tenant() -> None:
    session = _FakeSession()
    token = set_current_context(RequestContext(request_id="req_test", tenant_id="tenant-a"))
    try:
        await execute_tenant_scoped(
            session,  # type: ignore[arg-type]
            "select * from records where tenant_id = :tenant_id and status = :status",
            {"status": "active"},
        )

        assert session.parameters == {"status": "active", "tenant_id": "tenant-a"}
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_tenant_scoped_sql_rejects_conflicting_tenant_parameter() -> None:
    token = set_current_context(RequestContext(request_id="req_test", tenant_id="tenant-a"))
    try:
        with pytest.raises(AppError) as exc_info:
            await execute_tenant_scoped(
                _FakeSession(),  # type: ignore[arg-type]
                "select * from records where tenant_id = :tenant_id",
                {"tenant_id": "tenant-b"},
            )

        assert exc_info.value.code == "TENANT_CONTEXT_CONFLICT"
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_cross_tenant_sql_requires_reason_and_platform_permission() -> None:
    with pytest.raises(AppError) as missing_reason:
        await execute_cross_tenant(
            _FakeSession(),  # type: ignore[arg-type]
            "select * from records",
            reason="",
            platform_permission_granted=True,
        )
    with pytest.raises(AppError) as missing_permission:
        await execute_cross_tenant(
            _FakeSession(),  # type: ignore[arg-type]
            "select * from records",
            reason="support export",
            platform_permission_granted=False,
        )

    assert missing_reason.value.code == "PERMISSION_DENIED"
    assert missing_permission.value.code == "PERMISSION_DENIED"


class _FakeSession:
    def __init__(self) -> None:
        self.statement: Any | None = None
        self.parameters: dict[str, Any] | None = None

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> str:
        self.statement = statement
        self.parameters = parameters
        return "ok"
