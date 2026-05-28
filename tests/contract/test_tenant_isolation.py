from typing import Any

import pytest
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import IdMixin, SoftDeleteMixin, TenantScopedModel, TimestampMixin
from core.base.repositories import CrossTenantRepository, TenantScopedRepository
from core.context import RequestContext, reset_current_context, set_current_context
from core.db.constraints import check_tenant_scoped_model
from core.exceptions import AppError
from core.permissions import PLATFORM_TENANT_ID, AuthorizationDecision


class TenantThing(IdMixin, SoftDeleteMixin, TimestampMixin, TenantScopedModel):
    __tablename__ = "test_tenant_things"

    name: Mapped[str] = mapped_column(String(64), nullable=False)


class BadUniqueThing(IdMixin, TenantScopedModel):
    __tablename__ = "test_bad_unique_things"

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class TenantThingRepository(TenantScopedRepository[TenantThing]):
    model = TenantThing


class TenantThingCrossRepository(CrossTenantRepository[TenantThing]):
    model = TenantThing


def test_tenant_scoped_query_injects_tenant_and_soft_delete_filter() -> None:
    token = set_current_context(RequestContext(request_id="req_test", tenant_id="tenant-a"))
    try:
        repo = TenantThingRepository(_FakeSession())  # type: ignore[arg-type]

        sql = str(repo.query().compile(compile_kwargs={"literal_binds": True}))

        assert "test_tenant_things.tenant_id = 'tenant-a'" in sql
        assert "test_tenant_things.deleted_at IS NULL" in sql
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_tenant_scoped_create_writes_current_tenant() -> None:
    session = _FakeSession()
    token = set_current_context(RequestContext(request_id="req_test", tenant_id="tenant-a"))
    try:
        repo = TenantThingRepository(session)  # type: ignore[arg-type]

        record = await repo.create({"name": "demo"})

        assert record.tenant_id == "tenant-a"
        assert session.added == [record]
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_tenant_scoped_create_rejects_cross_tenant_payload() -> None:
    token = set_current_context(RequestContext(request_id="req_test", tenant_id="tenant-a"))
    try:
        repo = TenantThingRepository(_FakeSession())  # type: ignore[arg-type]

        with pytest.raises(AppError) as exc_info:
            await repo.create({"tenant_id": "tenant-b", "name": "demo"})

        assert exc_info.value.code == "TENANT_CONTEXT_CONFLICT"
    finally:
        reset_current_context(token)


def test_cross_tenant_repository_requires_reason_and_platform_decision() -> None:
    decision = _platform_decision()
    with pytest.raises(AppError) as missing_reason:
        TenantThingCrossRepository(
            _FakeSession(),  # type: ignore[arg-type]
            reason="",
            platform_decision=decision,
        )
    with pytest.raises(AppError) as missing_permission:
        TenantThingCrossRepository(
            _FakeSession(),  # type: ignore[arg-type]
            reason="support export",
            platform_decision=AuthorizationDecision(
                allowed=False,
                tenant_id=PLATFORM_TENANT_ID,
                user_id="admin-1",
                resource="cross_tenant",
                action="read",
                reason="missing_projected_policy",
            ),
        )
    with pytest.raises(AppError) as tenant_decision:
        TenantThingCrossRepository(
            _FakeSession(),  # type: ignore[arg-type]
            reason="support export",
            platform_decision=AuthorizationDecision(
                allowed=True,
                tenant_id="tenant-a",
                user_id="admin-1",
                resource="cross_tenant",
                action="read",
                reason="matched_projected_policy",
            ),
        )

    repo = TenantThingCrossRepository(
        _FakeSession(),  # type: ignore[arg-type]
        reason="support export",
        platform_decision=decision,
    )

    assert missing_reason.value.code == "PERMISSION_DENIED"
    assert missing_permission.value.code == "PERMISSION_DENIED"
    assert tenant_decision.value.code == "PERMISSION_DENIED"
    assert repo.reason == "support export"
    assert repo.platform_decision == decision


def test_tenant_scoped_model_constraints_detect_global_unique_keys() -> None:
    assert check_tenant_scoped_model(TenantThing) == []

    violations = check_tenant_scoped_model(BadUniqueThing)

    assert any("must include tenant_id" in violation for violation in violations)


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, record: Any) -> None:
        self.added.append(record)


def _platform_decision() -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=True,
        tenant_id=PLATFORM_TENANT_ID,
        user_id="admin-1",
        resource="cross_tenant",
        action="read",
        reason="matched_projected_policy",
        policy_version=1,
    )
