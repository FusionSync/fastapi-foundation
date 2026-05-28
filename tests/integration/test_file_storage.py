from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.base.models import BaseModel
from core.db import unit_of_work
from core.exceptions import AppError
from core.storage import LocalStorageProvider
from platform_apps.files import FileObject, FileService


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
async def test_upload_writes_storage_and_metadata(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        file_object = await FileService(uow.session, storage).upload_bytes(
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            file_name="proposal.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=b"docx-bytes",
            file_type="upload",
        )

    assert file_object.tenant_id == "tenant-a"
    assert file_object.bucket == "local-files"
    assert file_object.object_key == f"tenants/tenant-a/files/{file_object.id}/original.bin"
    assert file_object.size == len(b"docx-bytes")
    assert file_object.checksum
    assert file_object.status == "available"
    assert await storage.get_file(file_object.object_key) == b"docx-bytes"

    async with session_factory() as session:
        persisted = await session.get(FileObject, file_object.id)
        assert persisted is not None
        assert persisted.object_key == file_object.object_key


@pytest.mark.asyncio
async def test_download_enforces_tenant_owner_and_lifecycle_gate(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        file_object = await FileService(uow.session, storage).upload_bytes(
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            file_name="proposal.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=b"docx-bytes",
            file_type="upload",
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        download = await FileService(uow.session, storage).download_bytes(
            file_id=file_object.id,
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            tenant_status="active",
        )

    assert download.file_name == "proposal.docx"
    assert download.content_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert download.data == b"docx-bytes"

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as tenant_error:
            await FileService(uow.session, storage).download_bytes(
                file_id=file_object.id,
                tenant_id="tenant-b",
                owner_type="bid",
                owner_id="bid-1",
            )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as owner_error:
            await FileService(uow.session, storage).download_bytes(
                file_id=file_object.id,
                tenant_id="tenant-a",
                owner_type="project",
                owner_id="project-1",
            )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as lifecycle_error:
            await FileService(uow.session, storage).download_bytes(
                file_id=file_object.id,
                tenant_id="tenant-a",
                owner_type="bid",
                owner_id="bid-1",
                tenant_status="deleting",
            )

    assert tenant_error.value.code == "TENANT_CONTEXT_CONFLICT"
    assert owner_error.value.code == "PERMISSION_DENIED"
    assert lifecycle_error.value.code == "TENANT_STATE_FORBIDDEN"


@pytest.mark.asyncio
async def test_delete_marks_metadata_and_removes_storage_object(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    storage = LocalStorageProvider(root=tmp_path, bucket="local-files")
    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        file_object = await FileService(uow.session, storage).upload_bytes(
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
            file_name="proposal.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=b"docx-bytes",
            file_type="upload",
        )

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        await FileService(uow.session, storage).delete_file(
            file_id=file_object.id,
            tenant_id="tenant-a",
            owner_type="bid",
            owner_id="bid-1",
        )

    async with session_factory() as session:
        persisted = await session.scalar(select(FileObject).where(FileObject.id == file_object.id))
        assert persisted is not None
        assert persisted.status == "deleted"
    assert await storage.exists(file_object.object_key) is False

    async with unit_of_work(session_factory) as uow:
        assert uow.session is not None
        with pytest.raises(AppError) as download_deleted:
            await FileService(uow.session, storage).download_bytes(
                file_id=file_object.id,
                tenant_id="tenant-a",
                owner_type="bid",
                owner_id="bid-1",
            )

    assert download_deleted.value.code == "NOT_FOUND"
