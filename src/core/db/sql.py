from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import get_current_context
from core.exceptions import AppError
from core.permissions.decisions import AuthorizationDecision, assert_platform_decision


async def execute_tenant_scoped(
    session: AsyncSession,
    statement: str,
    parameters: Mapping[str, Any] | None = None,
    *,
    tenant_id: str | None = None,
) -> Any:
    resolved_tenant_id = tenant_id or _current_tenant_id()
    _validate_tenant_predicate(statement)
    params = dict(parameters or {})
    provided_tenant_id = params.get("tenant_id")
    if provided_tenant_id is not None and provided_tenant_id != resolved_tenant_id:
        raise AppError(
            "TENANT_CONTEXT_CONFLICT",
            "Raw SQL tenant parameter conflicts with current tenant",
            status_code=403,
        )
    params["tenant_id"] = resolved_tenant_id
    return await session.execute(text(statement), params)


def _validate_tenant_predicate(statement: str) -> None:
    normalized = " ".join(statement.lower().split())
    if "tenant_id" in normalized and ":tenant_id" in normalized:
        return
    raise AppError(
        "TENANT_ACCESS_DENIED",
        "Tenant-scoped SQL must include an explicit tenant_id predicate",
        status_code=403,
    )


async def execute_cross_tenant(
    session: AsyncSession,
    statement: str,
    parameters: Mapping[str, Any] | None = None,
    *,
    reason: str,
    platform_decision: AuthorizationDecision,
) -> Any:
    if not reason.strip():
        raise AppError(
            "PERMISSION_DENIED",
            "Cross-tenant SQL requires an audit reason",
            status_code=403,
        )
    assert_platform_decision(platform_decision)
    return await session.execute(text(statement), dict(parameters or {}))


def _current_tenant_id() -> str:
    context = get_current_context()
    if not context or not context.tenant_id:
        raise AppError(
            "TENANT_ACCESS_DENIED",
            "Tenant-scoped SQL requires tenant context",
            status_code=403,
        )
    return context.tenant_id
