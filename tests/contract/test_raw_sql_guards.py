from typing import Any

import pytest

from core.context import RequestContext, reset_current_context, set_current_context
from core.db.sql import execute_cross_tenant, execute_tenant_scoped
from core.exceptions import AppError
from core.permissions import PLATFORM_TENANT_ID, AuthorizationDecision


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
async def test_tenant_scoped_sql_rejects_missing_tenant_predicate() -> None:
    token = set_current_context(RequestContext(request_id="req_test", tenant_id="tenant-a"))
    try:
        with pytest.raises(AppError) as exc_info:
            await execute_tenant_scoped(
                _FakeSession(),  # type: ignore[arg-type]
                "select * from records where status = :status",
                {"status": "active"},
            )

        assert exc_info.value.code == "TENANT_ACCESS_DENIED"
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_tenant_scoped_sql_rejects_tenant_predicate_in_comment() -> None:
    token = set_current_context(RequestContext(request_id="req_test", tenant_id="tenant-a"))
    try:
        with pytest.raises(AppError) as exc_info:
            await execute_tenant_scoped(
                _FakeSession(),  # type: ignore[arg-type]
                "select * from records where status = :status -- tenant_id = :tenant_id",
                {"status": "active"},
            )

        assert exc_info.value.code == "TENANT_ACCESS_DENIED"
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_tenant_scoped_sql_rejects_tenant_id_substring_column() -> None:
    token = set_current_context(RequestContext(request_id="req_test", tenant_id="tenant-a"))
    try:
        with pytest.raises(AppError) as exc_info:
            await execute_tenant_scoped(
                _FakeSession(),  # type: ignore[arg-type]
                "select * from records where organization_tenant_id = :tenant_id",
            )

        assert exc_info.value.code == "TENANT_ACCESS_DENIED"
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_tenant_scoped_sql_accepts_alias_qualified_tenant_predicate() -> None:
    session = _FakeSession()
    token = set_current_context(RequestContext(request_id="req_test", tenant_id="tenant-a"))
    try:
        await execute_tenant_scoped(
            session,  # type: ignore[arg-type]
            "select * from records r where r.tenant_id = :tenant_id",
        )

        assert session.parameters == {"tenant_id": "tenant-a"}
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_cross_tenant_sql_requires_reason_and_platform_decision() -> None:
    decision = _platform_decision()
    with pytest.raises(AppError) as missing_reason:
        await execute_cross_tenant(
            _FakeSession(),  # type: ignore[arg-type]
            "select * from records",
            reason="",
            platform_decision=decision,
        )
    with pytest.raises(AppError) as denied_decision:
        await execute_cross_tenant(
            _FakeSession(),  # type: ignore[arg-type]
            "select * from records",
            reason="support export",
            platform_decision=AuthorizationDecision(
                allowed=False,
                tenant_id=PLATFORM_TENANT_ID,
                user_id="admin-1",
                resource="cross_tenant",
                action="read",
                reason="missing_projected_policy",
            ),
        )
    with pytest.raises(AppError) as tenant_decision:
        await execute_cross_tenant(
            _FakeSession(),  # type: ignore[arg-type]
            "select * from records",
            reason="support export",
            platform_decision=AuthorizationDecision(
                allowed=True,
                tenant_id="tenant-a",
                user_id="admin-1",
                resource="cross_tenant",
                action="read",
                reason="matched_projected_policy",
            ),
        )

    session = _FakeSession()
    await execute_cross_tenant(
        session,  # type: ignore[arg-type]
        "select * from records",
        reason="support export",
        platform_decision=decision,
    )

    assert missing_reason.value.code == "PERMISSION_DENIED"
    assert denied_decision.value.code == "PERMISSION_DENIED"
    assert tenant_decision.value.code == "PERMISSION_DENIED"
    assert session.parameters == {}


def _platform_decision() -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        tenant_id=PLATFORM_TENANT_ID,
        user_id="admin-1",
        resource="cross_tenant",
        action="read",
        reason="matched_projected_policy",
        policy_version=1,
    )


class _FakeSession:
    def __init__(self) -> None:
        self.statement: Any | None = None
        self.parameters: dict[str, Any] | None = None

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> str:
        self.statement = statement
        self.parameters = parameters
        return "ok"
