from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import APIRouter, Depends
from starlette.requests import Request

from core.context import RequestContext, get_current_context
from core.exceptions import AppError

CORE_ROUTE_SECURITY_POLICY_ATTR = "_core_route_security_policy"
CORE_ROUTE_AUTHORIZATION_RESULT_ATTR = "_core_route_authorization_result"
RouteAuthorizer = Callable[[RequestContext | None, "RouteSecurityPolicy"], object]
RequestSecurityResolver = Callable[[Request, "RouteSecurityPolicy"], object]
PermissionScope = Literal["tenant", "platform"]


@dataclass(frozen=True, slots=True)
class RouteSecurityPolicy:
    public: bool = False
    auth_required: bool = True
    tenant_required: bool = True
    permissions: tuple[str, ...] = field(default_factory=tuple)
    permission_scope: PermissionScope | None = None
    tenant_operation: str = "read"

    def to_dict(self) -> dict[str, Any]:
        return {
            "public": self.public,
            "auth_required": self.auth_required,
            "tenant_required": self.tenant_required,
            "permissions": list(self.permissions),
            "permission_scope": self.permission_scope,
            "tenant_operation": self.tenant_operation,
        }


def create_router(
    prefix: str,
    tags: list[str] | None = None,
    *,
    public: bool = False,
    auth_required: bool = True,
    tenant_required: bool = True,
    permissions: list[str] | tuple[str, ...] | None = None,
    permission_scope: PermissionScope | None = None,
    tenant_operation: str = "read",
) -> APIRouter:
    policy = _build_route_security_policy(
        public=public,
        auth_required=auth_required,
        tenant_required=tenant_required,
        permissions=permissions,
        permission_scope=permission_scope,
        tenant_operation=tenant_operation,
    )
    dependencies = [] if policy.public else [Depends(_route_security_dependency(policy))]
    router = APIRouter(prefix=prefix, tags=tags or [], dependencies=dependencies)
    setattr(router, CORE_ROUTE_SECURITY_POLICY_ATTR, policy)
    return router


def get_router_security_policy(router: APIRouter) -> RouteSecurityPolicy | None:
    policy = getattr(router, CORE_ROUTE_SECURITY_POLICY_ATTR, None)
    if isinstance(policy, RouteSecurityPolicy):
        return policy
    return None


def parse_route_permission(permission: str) -> tuple[str, str]:
    resource, separator, action = permission.rpartition(":")
    if not separator or not resource.strip() or not action.strip():
        raise AppError(
            "VALIDATION_ERROR",
            "Route permission must use resource:action format",
            status_code=400,
            details={"permission": permission},
        )
    return resource, action


async def enforce_route_security(
    policy: RouteSecurityPolicy,
    *,
    authorizer: RouteAuthorizer | None = None,
) -> object | None:
    if policy.public:
        return None
    context = get_current_context()
    if policy.auth_required and (context is None or not context.user_id):
        raise AppError(
            "AUTH_INVALID_TOKEN",
            "Authentication is required",
            status_code=401,
        )
    if policy.tenant_required and (context is None or not context.tenant_id):
        raise AppError(
            "TENANT_ACCESS_DENIED",
            "Tenant context is required",
            status_code=403,
        )
    if not policy.permissions:
        return None
    if authorizer is None:
        raise AppError(
            "PERMISSION_DENIED",
            "Route permissions require an authorizer",
            status_code=403,
            details={"permissions": list(policy.permissions)},
        )
    result = authorizer(context, policy)
    if inspect.isawaitable(result):
        result = await result
    return result


def _build_route_security_policy(
    *,
    public: bool,
    auth_required: bool,
    tenant_required: bool,
    permissions: list[str] | tuple[str, ...] | None,
    permission_scope: PermissionScope | None,
    tenant_operation: str,
) -> RouteSecurityPolicy:
    if permission_scope is not None and permission_scope not in {"tenant", "platform"}:
        raise ValueError("permission_scope must be tenant or platform")
    resolved_permissions = tuple(permissions or ())
    resolved_permission_scope = (
        permission_scope if resolved_permissions else None
    ) or ("tenant" if resolved_permissions else None)
    if public:
        if permissions:
            raise ValueError("public routers cannot declare permissions")
        if permission_scope is not None:
            raise ValueError("public routers cannot declare permission_scope")
        return RouteSecurityPolicy(
            public=True,
            auth_required=False,
            tenant_required=False,
            permissions=(),
            permission_scope=None,
            tenant_operation=tenant_operation,
        )
    return RouteSecurityPolicy(
        public=False,
        auth_required=auth_required,
        tenant_required=tenant_required,
        permissions=resolved_permissions,
        permission_scope=resolved_permission_scope,
        tenant_operation=tenant_operation,
    )


def _route_security_dependency(policy: RouteSecurityPolicy):
    async def dependency(request: Request) -> None:
        resolver = getattr(request.app.state, "request_security_resolver", None)
        if resolver is not None and not policy.public:
            resolved = resolver(request, policy)
            if inspect.isawaitable(resolved):
                await resolved
        authorization_result = await enforce_route_security(
            policy,
            authorizer=getattr(request.app.state, "route_authorizer", None),
        )
        if authorization_result is not None:
            setattr(
                request.state,
                CORE_ROUTE_AUTHORIZATION_RESULT_ATTR,
                authorization_result,
            )

    return dependency
