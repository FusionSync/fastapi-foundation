from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.base import RouteSecurityPolicy, create_router
from core.context import RequestContext
from core.exceptions import AppError, register_exception_handlers
from core.serialization import ok


def test_route_permissions_call_configured_authorizer() -> None:
    calls: list[tuple[RequestContext | None, RouteSecurityPolicy]] = []
    app = FastAPI()
    router = create_router(
        "/secure",
        auth_required=False,
        tenant_required=False,
        permissions=["runtime:read"],
    )

    @router.get("/ping")
    async def ping() -> dict[str, object]:
        return ok({"status": "ok"})

    app.state.route_authorizer = lambda context, policy: calls.append((context, policy))
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/secure/ping")

    assert response.status_code == 200
    assert [call[1].permissions for call in calls] == [("runtime:read",)]


def test_route_permission_authorizer_can_reject_request() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    router = create_router(
        "/secure",
        auth_required=False,
        tenant_required=False,
        permissions=["runtime:read"],
    )

    @router.get("/ping")
    async def ping() -> dict[str, object]:
        return ok({"status": "ok"})

    def reject(context: RequestContext | None, policy: RouteSecurityPolicy) -> None:
        raise AppError(
            "PERMISSION_DENIED",
            "denied by route authorizer",
            status_code=403,
            details={"permissions": list(policy.permissions)},
        )

    app.state.route_authorizer = reject
    app.include_router(router)
    client = TestClient(app)

    response = client.get("/secure/ping")

    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    assert response.json()["details"] == {"permissions": ["runtime:read"]}
