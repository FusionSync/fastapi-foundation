from __future__ import annotations

from collections.abc import Sequence

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
