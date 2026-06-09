from __future__ import annotations

from collections.abc import Sequence

from fastapi import Depends
from starlette.requests import Request

from core.base.routers import CORE_ROUTE_AUTHORIZATION_RESULT_ATTR
from core.exceptions import AppError
from core.permissions.context import AuthorizationDecisionSet
from core.permissions.decisions import AuthorizationDecision


def route_authorization_decisions(request: Request) -> tuple[AuthorizationDecision, ...]:
    result = getattr(request.state, CORE_ROUTE_AUTHORIZATION_RESULT_ATTR, None)
    decisions = _normalize_route_authorization_result(result)
    if decisions:
        return decisions
    raise AppError(
        "PERMISSION_DENIED",
        "Route authorization decision is required",
        status_code=403,
        details={"reason": "missing_route_authorization_decision"},
    )


def route_authorization_decision(request: Request) -> AuthorizationDecision:
    return route_authorization_decisions(request)[0]


def route_authorization_decision_for(permission: str, *, scope: str | None = None):
    def dependency(request: Request) -> AuthorizationDecision:
        return AuthorizationDecisionSet(route_authorization_decisions(request)).require(
            permission,
            scope=scope,
        )

    return dependency


def require_permission(permission: str, *, scope: str | None = None):
    return Depends(route_authorization_decision_for(permission, scope=scope))


def require_any_permission(permissions: Sequence[str], *, scope: str | None = None):
    resolved_permissions = _normalize_permissions(permissions)

    def dependency(request: Request) -> AuthorizationDecision:
        decision_set = AuthorizationDecisionSet(route_authorization_decisions(request))
        for permission in resolved_permissions:
            try:
                return decision_set.require(permission, scope=scope)
            except AppError as exc:
                if exc.code == "VALIDATION_ERROR":
                    raise
        raise AppError(
            "PERMISSION_DENIED",
            "Route authorization decision is required",
            status_code=403,
            details={
                "reason": "missing_route_authorization_decision",
                "permissions": list(resolved_permissions),
            },
        )

    return Depends(dependency)


def _normalize_route_authorization_result(
    result: object,
) -> tuple[AuthorizationDecision, ...]:
    if result is None:
        return ()
    if isinstance(result, AuthorizationDecision):
        return (result,)
    if isinstance(result, Sequence) and not isinstance(result, str | bytes | bytearray):
        if all(isinstance(item, AuthorizationDecision) for item in result):
            return tuple(result)
    raise AppError(
        "PERMISSION_DENIED",
        "Route authorizer must return AuthorizationDecision",
        status_code=403,
        details={"reason": "invalid_route_authorization_decision"},
    )


def _normalize_permissions(permissions: Sequence[str]) -> tuple[str, ...]:
    if isinstance(permissions, str | bytes | bytearray):
        raise AppError(
            "VALIDATION_ERROR",
            "permissions must be a non-empty sequence of resource:action strings",
            status_code=400,
        )
    resolved = tuple(permission.strip() for permission in permissions if permission.strip())
    if not resolved:
        raise AppError(
            "VALIDATION_ERROR",
            "permissions must be a non-empty sequence of resource:action strings",
            status_code=400,
        )
    return resolved
