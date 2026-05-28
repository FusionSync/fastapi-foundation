from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from core.auth.errors import invalid_auth_token
from core.auth.jwt_provider import LocalJwtProvider
from core.auth.session import AuthSessionStore, AuthSessionValidator
from core.base import RouteSecurityPolicy
from core.context import RequestContext
from core.exceptions import AppError
from core.permissions import AuthorizationDecision, AuthorizationService
from core.tenancy import DatabaseTenantContextResolver

SessionStoreFactory = Callable[[AsyncSession], AuthSessionStore]


class DatabaseRequestSecurityPipeline:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        jwt_provider: LocalJwtProvider,
        session_store_factory: SessionStoreFactory,
    ) -> None:
        self.session_factory = session_factory
        self.jwt_provider = jwt_provider
        self.session_store_factory = session_store_factory

    async def resolve(self, request: Request, policy: RouteSecurityPolicy) -> None:
        if not (policy.auth_required or policy.tenant_required or policy.permissions):
            return
        token = _bearer_token(request)
        claims = self.jwt_provider.verify_token(token)
        async with self.session_factory() as session:
            current_user = await AuthSessionValidator(
                self.session_store_factory(session)
            ).authenticate(claims)
            await DatabaseTenantContextResolver(session).resolve(
                current_user=current_user,
                token_tenant_id=claims.tenant_id,
                header_tenant_id=request.headers.get("X-Tenant-ID"),
                operation=policy.tenant_operation,  # type: ignore[arg-type]
            )

    async def authorize(
        self,
        context: RequestContext | None,
        policy: RouteSecurityPolicy,
    ) -> tuple[AuthorizationDecision, ...]:
        if not policy.permissions:
            return ()
        if context is None or not context.user_id or not context.tenant_id:
            raise AppError(
                "TENANT_ACCESS_DENIED",
                "Tenant context is required before permission authorization",
                status_code=403,
            )
        async with self.session_factory() as session:
            authorization = AuthorizationService(session)
            decisions: list[AuthorizationDecision] = []
            for permission in policy.permissions:
                resource, action = parse_route_permission(permission)
                decisions.append(
                    await authorization.require(
                        user_id=context.user_id,
                        tenant_id=context.tenant_id,
                        resource=resource,
                        action=action,
                        request_id=context.request_id,
                    )
                )
            return tuple(decisions)


def parse_route_permission(permission: str) -> tuple[str, str]:
    resource, separator, action = permission.partition(":")
    if not separator or not resource.strip() or not action.strip():
        raise AppError(
            "VALIDATION_ERROR",
            "Route permission must use resource:action format",
            status_code=400,
            details={"permission": permission},
        )
    return resource, action


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization")
    if authorization is None:
        invalid_auth_token("missing_bearer_token")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        invalid_auth_token("invalid_authorization_header")
    return token.strip()
