from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.context import get_current_context
from core.exceptions import AppError
from core.permissions.cross_tenant import CrossTenantPermission, cross_tenant_reason_and_decision
from core.permissions.decisions import AuthorizationDecision

_SQL_COMMENT_PATTERN = re.compile(r"(--[^\r\n]*(?:\r?\n|$))|(/\*.*?\*/)", re.DOTALL)
_SQL_SINGLE_QUOTED_STRING_PATTERN = re.compile(r"'(?:''|[^'])*'")
_TENANT_COLUMN_PATTERN = r'(?:(?:[a-z_][a-z0-9_]*|"[^"]+")\s*\.\s*)?(?:"tenant_id"|tenant_id)'
_TENANT_BIND_PATTERN = r":tenant_id\b"
_TENANT_PREDICATE_PATTERN = re.compile(
    rf"(?<![a-z0-9_])(?:"
    rf"{_TENANT_COLUMN_PATTERN}\s*=\s*{_TENANT_BIND_PATTERN}"
    rf"|{_TENANT_BIND_PATTERN}\s*=\s*{_TENANT_COLUMN_PATTERN}"
    rf")",
    re.IGNORECASE,
)


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
    normalized = _strip_sql_comments_and_literals(statement)
    if _TENANT_PREDICATE_PATTERN.search(normalized):
        return
    raise AppError(
        "TENANT_ACCESS_DENIED",
        "Tenant-scoped SQL must include an explicit tenant_id predicate",
        status_code=403,
    )


def _strip_sql_comments_and_literals(statement: str) -> str:
    without_comments = _SQL_COMMENT_PATTERN.sub(" ", statement)
    return _SQL_SINGLE_QUOTED_STRING_PATTERN.sub(" ", without_comments)


async def execute_cross_tenant(
    session: AsyncSession,
    statement: str,
    parameters: Mapping[str, Any] | None = None,
    *,
    reason: str | None = None,
    platform_decision: AuthorizationDecision | None = None,
    platform_access: CrossTenantPermission | None = None,
) -> Any:
    cross_tenant_reason_and_decision(
        reason=reason,
        platform_decision=platform_decision,
        platform_access=platform_access,
    )
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
