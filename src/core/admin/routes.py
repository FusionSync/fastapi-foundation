from __future__ import annotations

import html
import importlib
from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from core.admin.registry import AdminRegistry, RegisteredAdminPermission
from core.admin.specs import AdminPermissionSpec
from core.base import create_router


def build_admin_router(registry: AdminRegistry) -> APIRouter:
    router = APIRouter()
    if registry.admin_permissions:
        router.include_router(_build_console_router(registry))
    for route in registry.admin_routes:
        protected_router = create_router(
            route.spec.path,
            tags=["admin", route.app_label],
            tenant_required=False,
            permission_scope="platform",
            permissions=_permission_strings(route.spec.permissions),
        )
        protected_router.add_api_route(
            "",
            _load_handler(route.spec.handler_path),
            methods=list(route.spec.methods),
            name=route.spec.route_id,
        )
        router.include_router(protected_router)
    return router


def _build_console_router(registry: AdminRegistry) -> APIRouter:
    router = create_router(
        "/admin",
        tags=["admin"],
        tenant_required=False,
        permission_scope="platform",
        permissions=_registered_permission_strings(registry.admin_permissions),
    )

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    async def admin_console() -> HTMLResponse:
        return HTMLResponse(_render_console_html(registry))

    return router


def _load_handler(handler_path: str) -> Callable:
    module_path, separator, attribute_name = handler_path.rpartition(".")
    if not separator or not module_path or not attribute_name:
        raise ValueError(f"Invalid admin route handler path: {handler_path!r}")
    module = importlib.import_module(module_path)
    handler = getattr(module, attribute_name)
    if not callable(handler):
        raise TypeError(f"admin route handler {handler_path!r} must be callable")
    return handler


def _registered_permission_strings(
    permissions: list[RegisteredAdminPermission],
) -> list[str]:
    return _permission_strings([permission.spec for permission in permissions])


def _permission_strings(permissions: list[AdminPermissionSpec]) -> list[str]:
    return [
        f"{permission.to_permission_spec().resource}:{permission.action}"
        for permission in permissions
    ]


def _render_console_html(registry: AdminRegistry) -> str:
    payload = registry.to_dict()
    route_items = "".join(
        "<li>"
        f"{_escape(route['app_label'])} "
        f"{_escape(route['route_id'])} "
        f"<code>{_escape(route['path'])}</code>"
        "</li>"
        for route in payload["admin_routes"]
    )
    model_items = "".join(
        "<li>"
        f"{_escape(model['app_label'])} "
        f"{_escape(model['admin_id'])} "
        f"<code>{_escape(model['model_path'])}</code>"
        "</li>"
        for model in payload["model_admins"]
    )
    widget_items = "".join(
        "<li>"
        f"{_escape(widget['app_label'])} "
        f"{_escape(widget['widget_id'])} "
        f"{_escape(widget['title'])}"
        "</li>"
        for widget in payload["dashboard_widgets"]
    )
    return (
        "<!doctype html>"
        "<html lang=\"en\">"
        "<head><meta charset=\"utf-8\"><title>Admin</title></head>"
        "<body>"
        "<main>"
        "<h1>Admin</h1>"
        "<section><h2>Routes</h2><ul>"
        f"{route_items}"
        "</ul></section>"
        "<section><h2>Models</h2><ul>"
        f"{model_items}"
        "</ul></section>"
        "<section><h2>Widgets</h2><ul>"
        f"{widget_items}"
        "</ul></section>"
        "</main>"
        "</body>"
        "</html>"
    )


def _escape(value: object) -> str:
    return html.escape(str(value))
