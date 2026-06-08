from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace

from core.exceptions import AppError
from core.permissions.decisions import PLATFORM_TENANT_ID, AuthorizationDecision


@dataclass(frozen=True, slots=True)
class AuthorizationDecisionSet:
    decisions: tuple[AuthorizationDecision, ...] = ()

    def require(
        self,
        permission: str,
        *,
        scope: str | None = None,
    ) -> AuthorizationDecision:
        resource, action = _parse_permission(permission)
        for decision in self.decisions:
            if (
                decision.allowed
                and decision.resource == resource
                and decision.action == action
                and (scope is None or _scope_for_decision(decision) == scope)
            ):
                return decision
        raise AppError(
            "PERMISSION_DENIED",
            "Route authorization decision is required",
            status_code=403,
            details={"reason": "missing_route_authorization_decision"},
        )


@dataclass(frozen=True, slots=True)
class AccessContext:
    request_id: str
    user_id: str
    tenant_id: str | None = None
    decisions: tuple[AuthorizationDecision, ...] = ()

    @property
    def decision_set(self) -> AuthorizationDecisionSet:
        return AuthorizationDecisionSet(self.decisions)

    def with_decisions(self, decisions: tuple[AuthorizationDecision, ...]) -> AccessContext:
        return replace(self, decisions=(*self.decisions, *decisions))


_current_access: ContextVar[AccessContext | None] = ContextVar(
    "current_access_context",
    default=None,
)


def set_current_access(context: AccessContext | None) -> Token[AccessContext | None]:
    return _current_access.set(context)


def get_current_access() -> AccessContext | None:
    return _current_access.get()


def current_access() -> AccessContext:
    context = get_current_access()
    if context is None:
        raise AppError(
            "AUTH_INVALID_TOKEN",
            "Access context is required",
            status_code=401,
        )
    return context


def reset_current_access(token: Token[AccessContext | None]) -> None:
    _current_access.reset(token)


def append_access_decision(decision: AuthorizationDecision) -> AccessContext | None:
    return append_access_decisions((decision,))


def append_access_decisions(
    decisions: tuple[AuthorizationDecision, ...],
) -> AccessContext | None:
    context = get_current_access()
    if context is None:
        return None
    updated = context.with_decisions(decisions)
    set_current_access(updated)
    return updated


def _parse_permission(permission: str) -> tuple[str, str]:
    resource, separator, action = permission.rpartition(":")
    if not separator or not resource.strip() or not action.strip():
        raise AppError(
            "VALIDATION_ERROR",
            "Permission must use resource:action format",
            status_code=400,
            details={"permission": permission},
        )
    return resource, action


def _scope_for_decision(decision: AuthorizationDecision) -> str:
    return "platform" if decision.tenant_id == PLATFORM_TENANT_ID else "tenant"
