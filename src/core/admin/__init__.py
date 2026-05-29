from core.admin.registry import (
    AdminRegistry,
    RegisteredAdminPermission,
    RegisteredDashboardWidget,
    RegisteredModelAdmin,
    RegisteredRouteAdmin,
)
from core.admin.routes import build_admin_router
from core.admin.specs import (
    AdminDashboardWidgetSpec,
    AdminModelSpec,
    AdminPermissionSpec,
    AdminRouteSpec,
)

__all__ = [
    "AdminDashboardWidgetSpec",
    "AdminModelSpec",
    "AdminPermissionSpec",
    "AdminRegistry",
    "AdminRouteSpec",
    "RegisteredAdminPermission",
    "RegisteredDashboardWidget",
    "RegisteredModelAdmin",
    "RegisteredRouteAdmin",
    "build_admin_router",
]
