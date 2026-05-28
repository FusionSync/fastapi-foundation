from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from core.base import RouteSecurityPolicy, create_router
from core.context import RequestContext
from core.exceptions import AppError, register_exception_handlers
from core.permissions import AuthorizationDecision, route_authorization_decision
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


def test_route_dependency_exposes_authorization_decision() -> None:
    app = FastAPI()
    router = create_router(
        "/secure",
        auth_required=False,
        tenant_required=False,
        permissions=["runtime:write"],
        tenant_operation="write",
    )

    @router.post("/mutate")
    async def mutate(
        decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
    ) -> dict[str, object]:
        return ok(
            {
                "tenant_id": decision.tenant_id,
                "user_id": decision.user_id,
                "resource": decision.resource,
                "action": decision.action,
                "policy_version": decision.policy_version,
            }
        )

    app.state.route_authorizer = lambda _context, _policy: AuthorizationDecision(
        allowed=True,
        tenant_id="tenant-a",
        user_id="user-1",
        resource="runtime",
        action="write",
        reason="matched_projected_policy",
        policy_version=3,
    )
    app.include_router(router)
    client = TestClient(app)

    response = client.post("/secure/mutate")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "resource": "runtime",
        "action": "write",
        "policy_version": 3,
    }


def test_route_dependency_rejects_missing_authorization_decision() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    router = create_router("/secure", auth_required=False, tenant_required=False)

    @router.post("/mutate")
    async def mutate(
        decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
    ) -> dict[str, object]:
        return ok({"resource": decision.resource})

    app.include_router(router)
    client = TestClient(app)

    response = client.post("/secure/mutate")

    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    assert response.json()["details"] == {"reason": "missing_route_authorization_decision"}
