from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import Model
from core.db import execute_cross_tenant, unit_of_work
from core.exceptions import AppError
from core.permissions import (
    PLATFORM_TENANT_ID,
    AuthorizationDecision,
    AuthorizationService,
    CrossTenantPermission,
    CrossTenantPermissionGate,
    ProjectedPolicy,
    assert_cross_tenant_permission,
)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Model.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cross_tenant_gate_returns_reusable_platform_permission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        uow.session.add(
            ProjectedPolicy(
                tenant_id=PLATFORM_TENANT_ID,
                subject="user:admin-1",
                resource="cross_tenant",
                action="read",
                effect="allow",
                role_grant_id="grant-platform",
                policy_version=3,
            )
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        access = await CrossTenantPermissionGate(
            AuthorizationService(uow.session)
        ).require(
            user_id="admin-1",
            resource="cross_tenant",
            action="read",
            target_tenant_ids=("tenant-a", "tenant-b"),
            reason="support tenant export",
            resource_id="tenant-export",
            request_id="req-cross-tenant",
        )
        result = await execute_cross_tenant(
            uow.session,
            "select :tenant_count",
            {"tenant_count": len(access.target_tenant_ids)},
            platform_access=access,
        )

    assert access.reason == "support tenant export"
    assert access.target_tenant_ids == ("tenant-a", "tenant-b")
    assert access.decision.tenant_id == PLATFORM_TENANT_ID
    assert access.decision.policy_version == 3
    assert result.scalar_one() == 2


@pytest.mark.asyncio
async def test_cross_tenant_gate_rejects_missing_reason_and_targets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        gate = CrossTenantPermissionGate(AuthorizationService(uow.session))

        with pytest.raises(AppError) as missing_reason:
            await gate.require(
                user_id="admin-1",
                resource="cross_tenant",
                action="read",
                target_tenant_ids=("tenant-a",),
                reason=" ",
                request_id="req-missing-reason",
            )
        with pytest.raises(AppError) as missing_target:
            await gate.require(
                user_id="admin-1",
                resource="cross_tenant",
                action="read",
                target_tenant_ids=(),
                reason="support tenant export",
                request_id="req-missing-target",
            )

    assert missing_reason.value.code == "PERMISSION_DENIED"
    assert missing_target.value.code == "PERMISSION_DENIED"


def test_cross_tenant_permission_assertion_rejects_tenant_scoped_decision() -> None:
    access = CrossTenantPermission(
        decision=AuthorizationDecision(
            allowed=True,
            tenant_id="tenant-a",
            user_id="admin-1",
            resource="cross_tenant",
            action="read",
            reason="matched_projected_policy",
            policy_version=1,
        ),
        reason="support tenant export",
        target_tenant_ids=("tenant-b",),
        resource="cross_tenant",
        action="read",
        request_id="req-cross-tenant",
    )

    with pytest.raises(AppError) as rejected:
        assert_cross_tenant_permission(
            access,
            resource="cross_tenant",
            actions={"read"},
            target_tenant_id="tenant-b",
        )

    assert rejected.value.code == "PERMISSION_DENIED"
