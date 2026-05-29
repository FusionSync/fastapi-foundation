from core.base.routers import (
    RequestSecurityResolver,
    RouteAuthorizer,
    RouteSecurityPolicy,
    create_router,
    get_router_security_policy,
    parse_route_permission,
)
from core.base.schemas import (
    BaseSchema,
    CreateSchema,
    ListQuerySchema,
    ReadSchema,
    SortTerm,
    UpdateSchema,
)
from core.base.services import BaseService

_REPOSITORY_EXPORTS = {
    "BaseRepository",
    "CrossTenantRepository",
    "TenantScopedRepository",
}

__all__ = [
    "BaseSchema",
    "BaseRepository",
    "BaseService",
    "CrossTenantRepository",
    "CreateSchema",
    "ListQuerySchema",
    "ReadSchema",
    "RequestSecurityResolver",
    "RouteSecurityPolicy",
    "RouteAuthorizer",
    "SortTerm",
    "TenantScopedRepository",
    "UpdateSchema",
    "create_router",
    "get_router_security_policy",
    "parse_route_permission",
]


def __getattr__(name: str) -> object:
    if name in _REPOSITORY_EXPORTS:
        from core.base.repositories import (
            BaseRepository,
            CrossTenantRepository,
            TenantScopedRepository,
        )

        exports = {
            "BaseRepository": BaseRepository,
            "CrossTenantRepository": CrossTenantRepository,
            "TenantScopedRepository": TenantScopedRepository,
        }
        return exports[name]
    raise AttributeError(f"module 'core.base' has no attribute {name!r}")
