from core.permissions.authorization import AuthorizationService
from core.permissions.backends import (
    CachedPolicyDecisionBackend,
    CasbinEquivalentPolicyBackend,
    PolicyDecisionBackend,
    PolicyMatch,
    ProjectedPolicyBackend,
)
from core.permissions.cache import (
    DistributedPermissionCache,
    PermissionCache,
    PermissionCacheInvalidator,
    invalidate_permission_cache,
)
from core.permissions.context import (
    AccessContext,
    AuthorizationDecisionSet,
    append_access_decision,
    append_access_decisions,
    current_access,
    get_current_access,
    reset_current_access,
    set_current_access,
)
from core.permissions.cross_tenant import (
    CrossTenantPermission,
    CrossTenantPermissionGate,
    assert_cross_tenant_permission,
)
from core.permissions.decisions import (
    PLATFORM_TENANT_ID,
    AuthorizationDecision,
    assert_authorization_decision,
    assert_platform_decision,
)
from core.permissions.deps import (
    require_any_permission,
    require_permission,
    route_authorization_decision,
    route_authorization_decision_for,
    route_authorization_decisions,
)
from core.permissions.models import ProjectedPolicy, RoleGrant, RoleTemplate
from core.permissions.policies import (
    PolicyRule,
    ReconciliationResult,
)
from core.permissions.projector import (
    ROLE_GRANT_CHANGED_EVENT,
    PolicyProjector,
)
from core.permissions.registry import PermissionRegistry, RegisteredPermission
from core.permissions.services import RoleGrantService
from core.permissions.specs import PermissionSpec

__all__ = [
    "AuthorizationDecision",
    "AuthorizationDecisionSet",
    "AuthorizationService",
    "AccessContext",
    "CachedPolicyDecisionBackend",
    "CasbinEquivalentPolicyBackend",
    "CrossTenantPermission",
    "CrossTenantPermissionGate",
    "DistributedPermissionCache",
    "PLATFORM_TENANT_ID",
    "ROLE_GRANT_CHANGED_EVENT",
    "PermissionCache",
    "PermissionCacheInvalidator",
    "PolicyDecisionBackend",
    "PolicyMatch",
    "PermissionRegistry",
    "PermissionSpec",
    "PolicyProjector",
    "PolicyRule",
    "ProjectedPolicyBackend",
    "ProjectedPolicy",
    "ReconciliationResult",
    "RegisteredPermission",
    "RoleGrant",
    "RoleGrantService",
    "RoleTemplate",
    "assert_cross_tenant_permission",
    "assert_authorization_decision",
    "assert_platform_decision",
    "append_access_decision",
    "append_access_decisions",
    "current_access",
    "get_current_access",
    "invalidate_permission_cache",
    "reset_current_access",
    "require_any_permission",
    "require_permission",
    "route_authorization_decision",
    "route_authorization_decision_for",
    "route_authorization_decisions",
    "set_current_access",
]
