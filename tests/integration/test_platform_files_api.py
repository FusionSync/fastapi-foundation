import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.app import create_app
from core.auth import LocalJwtConfig, LocalJwtProvider, TokenClaims
from core.base.models import BaseModel
from core.config import Settings
from core.db import unit_of_work
from core.permissions import ProjectedPolicy
from core.storage import LocalStorageProvider, MultipartUploadRequest, StoredObject
from core.tenancy import Tenant, TenantMember
from platform_apps.accounts.models import User, UserSession
from platform_apps.files import FileObject


def test_platform_files_api_supports_batch_and_presigned_uploads(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-files-api.db'}"
    asyncio.run(_seed_file_api_facts(database_url))
    app = create_app(
        Settings(
            database={"url": database_url},
            security={"jwt_secret": "test-secret"},
            installed_apps=[
                "platform_apps.accounts.module",
                "platform_apps.files.module",
            ],
        )
    )
    app.state.storage_provider = LocalStorageProvider(tmp_path / "files")
    client = TestClient(app)

    batch_response = client.post(
        "/api/v1/platform/files/batch",
        headers=_tenant_headers(),
        json={
            "owner_type": "project",
            "owner_id": "project-1",
            "files": [
                {
                    "file_name": "a.txt",
                    "content_type": "text/plain",
                    "file_type": "document",
                    "content_base64": base64.b64encode(b"alpha").decode("ascii"),
                },
                {
                    "file_name": "b.txt",
                    "content_type": "text/plain",
                    "file_type": "document",
                    "content_base64": base64.b64encode(b"beta").decode("ascii"),
                },
            ],
        },
    )

    assert batch_response.status_code == 200
    assert [item["file_name"] for item in batch_response.json()["data"]["files"]] == [
        "a.txt",
        "b.txt",
    ]

    presigned_response = client.post(
        "/api/v1/platform/files/presigned-upload",
        headers=_tenant_headers(),
        json={
            "owner_type": "project",
            "owner_id": "project-1",
            "file_name": "large.bin",
            "content_type": "application/octet-stream",
            "file_type": "archive",
            "expected_size": 1024,
            "expires_seconds": 600,
        },
    )

    assert presigned_response.status_code == 200
    presigned = presigned_response.json()["data"]
    assert presigned["file"]["status"] == "uploading"
    assert presigned["upload_url"].startswith("local://local-files/")
    assert presigned["expires_seconds"] == 600

    files = asyncio.run(_all_files(database_url))
    assert len(files) == 3
    assert sum(1 for file_object in files if file_object.status == "available") == 2
    assert sum(1 for file_object in files if file_object.status == "uploading") == 1


def test_platform_files_api_supports_multipart_large_upload_lifecycle(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-files-multipart.db'}"
    asyncio.run(_seed_file_api_facts(database_url))
    app = create_app(
        Settings(
            database={"url": database_url},
            security={"jwt_secret": "test-secret"},
            installed_apps=[
                "platform_apps.accounts.module",
                "platform_apps.files.module",
            ],
        )
    )
    storage = FakeMultipartStorage()
    app.state.storage_provider = storage
    client = TestClient(app)

    initiate_response = client.post(
        "/api/v1/platform/files/multipart",
        headers=_tenant_headers(),
        json={
            "owner_type": "project",
            "owner_id": "project-1",
            "file_name": "huge.bin",
            "content_type": "application/octet-stream",
            "file_type": "archive",
            "expected_size": 4096,
            "part_count": 3,
            "expires_seconds": 900,
        },
    )

    assert initiate_response.status_code == 200
    initiated = initiate_response.json()["data"]
    assert initiated["upload_id"] == "upload-huge"
    assert [part["part_number"] for part in initiated["parts"]] == [1, 2, 3]
    assert all(part["upload_url"].startswith("fake-part://") for part in initiated["parts"])

    complete_response = client.post(
        f"/api/v1/platform/files/multipart/{initiated['file']['id']}/complete",
        headers=_tenant_headers(),
        json={
            "upload_id": initiated["upload_id"],
            "parts": [
                {"part_number": 1, "etag": "etag-1"},
                {"part_number": 2, "etag": "etag-2"},
                {"part_number": 3, "etag": "etag-3"},
            ],
        },
    )

    assert complete_response.status_code == 200
    assert complete_response.json()["data"]["status"] == "available"
    assert storage.completed_uploads == [
        MultipartUploadRequest(
            object_key=initiated["file"]["object_key"],
            upload_id="upload-huge",
            parts=tuple(
                [
                    {"part_number": 1, "etag": "etag-1"},
                    {"part_number": 2, "etag": "etag-2"},
                    {"part_number": 3, "etag": "etag-3"},
                ]
            ),
        )
    ]


class FakeMultipartStorage:
    bucket = "fake-bucket"

    def __init__(self) -> None:
        self.completed_uploads = []

    async def put_file(self, object_key: str, data: bytes) -> StoredObject:
        return StoredObject(
            bucket=self.bucket,
            object_key=object_key,
            size=len(data),
            checksum="checksum",
        )

    async def get_file(self, object_key: str) -> bytes:
        return b""

    async def delete_file(self, object_key: str) -> None:
        return None

    async def exists(self, object_key: str) -> bool:
        return True

    async def generate_download_url(self, object_key: str, *, expires_seconds: int = 300) -> str:
        return f"fake-download://{object_key}?expires={expires_seconds}"

    async def generate_upload_url(
        self,
        object_key: str,
        *,
        content_type: str,
        expires_seconds: int = 300,
    ) -> str:
        return f"fake-upload://{object_key}?content_type={content_type}&expires={expires_seconds}"

    async def create_multipart_upload(self, object_key: str, *, content_type: str):
        return _Upload(object_key=object_key, upload_id="upload-huge")

    async def generate_multipart_part_url(
        self,
        *,
        object_key: str,
        upload_id: str,
        part_number: int,
        expires_seconds: int = 300,
    ) -> str:
        return f"fake-part://{object_key}/{upload_id}/{part_number}?expires={expires_seconds}"

    async def complete_multipart_upload(self, request):
        self.completed_uploads.append(request)
        return StoredObject(
            bucket=self.bucket,
            object_key=request.object_key,
            size=4096,
            checksum="multipart-checksum",
        )

    async def abort_multipart_upload(self, *, object_key: str, upload_id: str) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _Upload:
    object_key: str
    upload_id: str


async def _seed_file_api_facts(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                Tenant(
                    id="tenant-a",
                    name="Tenant A",
                    code="tenant-a",
                    status="active",
                    deployment_mode="local",
                )
            )
            uow.session.add(
                TenantMember(
                    tenant_id="tenant-a",
                    user_id="user-1",
                    status="active",
                )
            )
            uow.session.add(
                User(
                    id="user-1",
                    email="user@example.com",
                    display_name="User",
                    status="active",
                    token_version=1,
                )
            )
            uow.session.add(
                UserSession(
                    id="sess-user-1",
                    user_id="user-1",
                    tenant_id="tenant-a",
                    auth_provider="local",
                    status="active",
                    token_version=1,
                )
            )
            for action in ("upload", "download", "delete"):
                uow.session.add(
                    ProjectedPolicy(
                        tenant_id="tenant-a",
                        subject="user:user-1",
                        resource="file",
                        action=action,
                        effect="allow",
                        role_grant_id=f"grant-file-{action}",
                        policy_version=1,
                    )
                )
    finally:
        await engine.dispose()


def _tenant_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_tenant_token()}",
        "X-Tenant-ID": "tenant-a",
    }


def _tenant_token() -> str:
    return LocalJwtProvider(LocalJwtConfig(secret="test-secret")).issue_token(
        TokenClaims(
            user_id="user-1",
            session_id="sess-user-1",
            auth_provider="local",
            token_version=1,
            tenant_id="tenant-a",
        )
    )


async def _all_files(database_url: str):
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            rows = list((await session.execute(select(FileObject))).scalars().all())
            for row in rows:
                session.expunge(row)
            return rows
    finally:
        await engine.dispose()
