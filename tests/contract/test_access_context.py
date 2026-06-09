from types import SimpleNamespace

import pytest

from core.exceptions import AppError
from core.permissions import (
    AccessContext,
    AuthorizationDecision,
    AuthorizationDecisionSet,
    append_access_decision,
    get_current_access,
    require_any_permission,
    require_permission,
    reset_current_access,
    route_authorization_decision_for,
    set_current_access,
)


def test_authorization_decision_set_selects_by_permission_and_scope() -> None:
    tenant_decision = _decision(
        tenant_id="tenant-a",
        resource="tenant_invitation",
        action="invite",
    )
    role_grant_decision = _decision(
        tenant_id="tenant-a",
        resource="role_grant",
        action="grant",
    )

    decision_set = AuthorizationDecisionSet((tenant_decision, role_grant_decision))

    assert decision_set.require("role_grant:grant") is role_grant_decision
    assert decision_set.require("tenant_invitation:invite", scope="tenant") is tenant_decision


def test_authorization_decision_set_rejects_missing_permission() -> None:
    decision_set = AuthorizationDecisionSet((_decision(resource="tenant", action="manage"),))

    with pytest.raises(AppError) as exc_info:
        decision_set.require("role_grant:grant")

    assert exc_info.value.code == "PERMISSION_DENIED"
    assert exc_info.value.details == {"reason": "missing_route_authorization_decision"}


def test_route_authorization_decision_for_selects_named_decision() -> None:
    tenant_decision = _decision(resource="tenant_invitation", action="invite")
    role_grant_decision = _decision(resource="role_grant", action="grant")
    request = SimpleNamespace(
        state=SimpleNamespace(
            _core_route_authorization_result=(tenant_decision, role_grant_decision)
        )
    )

    dependency = route_authorization_decision_for("role_grant:grant")

    assert dependency(request) is role_grant_decision


def test_require_permission_returns_fastapi_dependency_for_named_decision() -> None:
    role_grant_decision = _decision(resource="role_grant", action="grant")
    request = SimpleNamespace(
        state=SimpleNamespace(_core_route_authorization_result=(role_grant_decision,))
    )

    dependency = require_permission("role_grant:grant")

    assert dependency.dependency(request) is role_grant_decision


def test_require_any_permission_returns_first_matching_decision() -> None:
    read_decision = _decision(resource="file", action="download")
    request = SimpleNamespace(
        state=SimpleNamespace(_core_route_authorization_result=(read_decision,))
    )

    dependency = require_any_permission(("file:delete", "file:download"))

    assert dependency.dependency(request) is read_decision


def test_require_any_permission_rejects_missing_permissions() -> None:
    request = SimpleNamespace(
        state=SimpleNamespace(
            _core_route_authorization_result=(_decision(resource="file", action="download"),)
        )
    )

    dependency = require_any_permission(("file:delete", "file:upload"))

    with pytest.raises(AppError) as exc_info:
        dependency.dependency(request)

    assert exc_info.value.code == "PERMISSION_DENIED"
    assert exc_info.value.details == {
        "reason": "missing_route_authorization_decision",
        "permissions": ["file:delete", "file:upload"],
    }


def test_access_context_appends_decisions_without_mutating_request_context() -> None:
    token = set_current_access(
        AccessContext(request_id="req-1", user_id="user-1", tenant_id="tenant-a")
    )
    try:
        decision = _decision()
        append_access_decision(decision)

        context = get_current_access()
        assert context is not None
        assert context.request_id == "req-1"
        assert context.decisions == (decision,)
    finally:
        reset_current_access(token)


def _decision(
    *,
    tenant_id: str = "tenant-a",
    resource: str = "example",
    action: str = "read",
) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        tenant_id=tenant_id,
        user_id="user-1",
        resource=resource,
        action=action,
        reason="matched_projected_policy",
        policy_version=1,
    )
