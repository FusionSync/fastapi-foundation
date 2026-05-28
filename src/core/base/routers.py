from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends
from starlette.requests import Request

from core.context import RequestContext, get_current_context
from core.exceptions import AppError

CORE_ROUTE_SECURITY_POLICY_ATTR = "_core_route_security_policy"
RouteAuthorizer = Callable[[RequestContext | None, "RouteSecurityPolicy"], object]


@dataclass(frozen=True, slots=True)
class RouteSecurityPolicy:
    public: bool = False
    auth_required: bool = True
    tenant_required: bool = True
    permissions: tuple[str, ...] = field(default_factory=tuple)
    tenant_operation: str = "read"

    def to_dict(self) -> dict[str, Any]:
        return {
            "public": self.public,
            "auth_required": self.auth_required,
            "tenant_required": self.tenant_required,
            "permissions": list(self.permissions),
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
    tenant_operation: str = "read",
) -> APIRouter:
    policy = _build_route_security_policy(
        public=public,
        auth_required=auth_required,
        tenant_required=tenant_required,
        permissions=permissions,
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


async def enforce_route_security(
    policy: RouteSecurityPolicy,
    *,
    authorizer: RouteAuthorizer | None = None,
) -> None:
    if policy.public:
        return
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
        return
    if authorizer is None:
        raise AppError(
            "PERMISSION_DENIED",
            "Route permissions require an authorizer",
            status_code=403,
            details={"permissions": list(policy.permissions)},
        )
    result = authorizer(context, policy)
    if inspect.isawaitable(result):
        await result


def _build_route_security_policy(
    *,
    public: bool,
    auth_required: bool,
    tenant_required: bool,
    permissions: list[str] | tuple[str, ...] | None,
    tenant_operation: str,
) -> RouteSecurityPolicy:
    if public:
        if permissions:
            raise ValueError("public routers cannot declare permissions")
        return RouteSecurityPolicy(
            public=True,
            auth_required=False,
            tenant_required=False,
            permissions=(),
            tenant_operation=tenant_operation,
        )
    return RouteSecurityPolicy(
        public=False,
        auth_required=auth_required,
        tenant_required=tenant_required,
        permissions=tuple(permissions or ()),
        tenant_operation=tenant_operation,
    )


def _route_security_dependency(policy: RouteSecurityPolicy):
    async def dependency(request: Request) -> None:
        await enforce_route_security(
            policy,
            authorizer=getattr(request.app.state, "route_authorizer", None),
        )

    return dependency
