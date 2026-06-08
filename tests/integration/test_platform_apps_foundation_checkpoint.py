from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.db import unit_of_work
from core.exceptions import AppError
from core.storage import LocalStorageProvider
from core.tasks import SyncTaskProvider, TaskEnvelope, TaskRegistry
from core.tenancy import (
    CurrentUser,
    Tenant,
    TenantMember,
    TenantMembership,
    TenantRecord,
    resolve_current_tenant,
)
from platform_apps.accounts import AccountsService
from platform_apps.audit import AuditLog, AuditService
from platform_apps.files import FileService


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_platform_apps_checkpoint_enforces_lifecycle_gates_and_session_audit(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        accounts = AccountsService(uow.session, audit=AuditService(uow.session))
        user = await accounts.create_user(
            email="owner@example.com",
            display_name="Owner",
        )
        _add_tenant_member(uow.session, tenant_id="tenant-a", user_id=user.id)
        user_session = await accounts.create_session(
            user_id=user.id,
            tenant_id="tenant-a",
            auth_provider="local",
            request_id="req-login",
        )

    audit_logs = await _audit_logs(session_factory)
    assert [(log.action, log.tenant_id, log.actor_id, log.session_id) for log in audit_logs] == [
        ("session.created", "tenant-a", user.id, user_session.id)
    ]

    suspended_user = CurrentUser(
        user_id=user.id,
        default_tenant_id="tenant-a",
        memberships=(TenantMembership(tenant_id="tenant-a"),),
    )
    suspended_tenant = TenantRecord(tenant_id="tenant-a", status="suspended")
    assert (
        resolve_current_tenant(
            current_user=suspended_user,
            tenant=suspended_tenant,
            operation="read",
        )
        == "tenant-a"
    )
    with pytest.raises(AppError) as write_rejected:
        resolve_current_tenant(
            current_user=suspended_user,
            tenant=suspended_tenant,
            operation="write",
        )

    with pytest.raises(AppError) as task_rejected:
        await SyncTaskProvider(TaskRegistry()).submit(
            TaskEnvelope(
                task_id="task-1",
                task_type="example.refresh",
                tenant_id="tenant-a",
                payload={},
                idempotency_key="idem-1",
                request_id="req-task",
            ),
            tenant_status="suspended",
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as file_rejected:
            await FileService(
                uow.session,
                LocalStorageProvider(root=tmp_path, bucket="local-files"),
            ).download_bytes(
                file_id="file-1",
                tenant_id="tenant-a",
                owner_type="example",
                owner_id="record-1",
                tenant_status="deleting",
            )

    assert write_rejected.value.code == "TENANT_STATE_FORBIDDEN"
    assert task_rejected.value.code == "TENANT_STATE_FORBIDDEN"
    assert file_rejected.value.code == "TENANT_STATE_FORBIDDEN"


async def _audit_logs(session_factory: async_sessionmaker[AsyncSession]) -> list[AuditLog]:
    async with session_factory() as session:
        result = await session.execute(select(AuditLog).order_by(AuditLog.created_at))
        audit_logs = list(result.scalars().all())
        for audit_log in audit_logs:
            session.expunge(audit_log)
        return audit_logs


def _add_tenant_member(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    session.add(
        Tenant(
            id=tenant_id,
            name=tenant_id,
            code=tenant_id,
            status="active",
            deployment_mode="local",
        )
    )
    session.add(TenantMember(tenant_id=tenant_id, user_id=user_id, status="active"))
