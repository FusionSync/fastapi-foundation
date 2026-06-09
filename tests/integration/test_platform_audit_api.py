import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.app import create_app
from core.auth import LocalJwtConfig, LocalJwtProvider, TokenClaims
from core.base.models import Model
from core.config import Settings
from core.db import unit_of_work
from core.permissions import PLATFORM_TENANT_ID, ProjectedPolicy
from platform_apps.accounts.models import User, UserSession
from platform_apps.audit import AuditService


def test_platform_audit_api_queries_verifies_and_exports_logs(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-audit-api.db'}"
    asyncio.run(_seed_audit_api_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.audit.module",
                ],
            )
        )
    )

    logs_response = client.get(
        "/api/v1/platform/audit/logs",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={"tenant_id": "tenant-a"},
    )

    assert logs_response.status_code == 200
    assert [item["resource_id"] for item in logs_response.json()["list"]] == [
        "tenant-a-first",
        "tenant-a-second",
    ]

    filtered_response = client.get(
        "/api/v1/platform/audit/logs",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={
            "tenant_id": "tenant-a",
            "actor_id": "user-2",
            "created_from": "2026-01-02T00:00:00Z",
            "created_to": "2026-01-02T23:59:59Z",
        },
    )
    assert filtered_response.status_code == 200
    assert [item["resource_id"] for item in filtered_response.json()["list"]] == [
        "tenant-a-second"
    ]

    verify_response = client.post(
        "/api/v1/platform/audit/verify",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={"tenant_id": "tenant-a"},
    )

    assert verify_response.status_code == 200
    assert verify_response.json()["data"] == {
        "tenant_id": "tenant-a",
        "checked": 2,
        "valid": True,
        "errors": [],
    }

    export_response = client.post(
        "/api/v1/platform/audit/exports",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={
            "tenant_id": "tenant-a",
            "destination_type": "worm",
            "export_id": "export-api",
            "destination_root": str(tmp_path / "exports"),
        },
    )

    assert export_response.status_code == 200
    export_record = export_response.json()["data"]
    assert export_record["id"] == "export-api"
    assert export_record["status"] == "succeeded"
    assert export_record["record_count"] == 2
    assert Path(export_record["destination_uri"]).is_file()

    exports_response = client.get(
        "/api/v1/platform/audit/exports",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={"tenant_id": "tenant-a"},
    )
    assert exports_response.status_code == 200
    assert ["export-api"] == [item["id"] for item in exports_response.json()["list"]]


def test_platform_audit_retention_policy_and_siem_export(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-audit-retention.db'}"
    asyncio.run(_seed_audit_api_facts(database_url))
    client = TestClient(
        create_app(
            Settings(
                database={"url": database_url},
                security={"jwt_secret": "test-secret"},
                installed_apps=[
                    "platform_apps.accounts.module",
                    "platform_apps.audit.module",
                ],
            )
        )
    )

    export_response = client.post(
        "/api/v1/platform/audit/exports",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={
            "tenant_id": "tenant-a",
            "destination_type": "siem",
            "export_id": "export-siem",
            "destination_root": str(tmp_path / "siem-exports"),
        },
    )
    assert export_response.status_code == 200
    export_record = export_response.json()["data"]
    assert export_record["destination_type"] == "siem"
    assert export_record["destination_uri"].endswith(".siem.jsonl")
    assert Path(export_record["destination_uri"]).is_file()

    preview_response = client.post(
        "/api/v1/platform/audit/retention",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={
            "tenant_id": "tenant-a",
            "older_than": "2026-01-03T00:00:00Z",
            "dry_run": True,
        },
    )
    assert preview_response.status_code == 200
    assert preview_response.json()["data"]["matched_count"] == 2
    assert preview_response.json()["data"]["deleted_count"] == 0
    assert preview_response.json()["data"]["chain_safe"] is True

    apply_response = client.post(
        "/api/v1/platform/audit/retention",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        json={
            "tenant_id": "tenant-a",
            "older_than": "2026-01-03T00:00:00Z",
            "dry_run": False,
        },
    )
    assert apply_response.status_code == 200
    assert apply_response.json()["data"]["matched_count"] == 2
    assert apply_response.json()["data"]["deleted_count"] == 2

    logs_response = client.get(
        "/api/v1/platform/audit/logs",
        headers={"Authorization": f"Bearer {_platform_token()}"},
        params={"tenant_id": "tenant-a"},
    )
    assert logs_response.status_code == 200
    assert logs_response.json()["list"] == []


async def _seed_audit_api_facts(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Model.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with unit_of_work(session_factory) as uow:
            assert uow.session is not None
            uow.session.add(
                User(
                    id="auditor-1",
                    email="auditor@example.com",
                    display_name="Auditor",
                    status="active",
                    token_version=1,
                )
            )
            uow.session.add(
                UserSession(
                    id="sess-auditor",
                    user_id="auditor-1",
                    tenant_id=None,
                    auth_provider="local",
                    status="active",
                    token_version=1,
                )
            )
            for action in ("read", "export"):
                uow.session.add(
                    ProjectedPolicy(
                        tenant_id=PLATFORM_TENANT_ID,
                        subject="user:auditor-1",
                        resource="audit_log",
                        action=action,
                        effect="allow",
                        role_grant_id=f"grant-audit-{action}",
                        policy_version=1,
                    )
                )
            first = await AuditService(uow.session).record(
                tenant_id="tenant-a",
                actor_id="user-1",
                action="tenant.first",
                resource_type="tenant",
                resource_id="tenant-a-first",
                result="success",
            )
            first.created_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
            second = await AuditService(uow.session).record(
                tenant_id="tenant-a",
                actor_id="user-2",
                action="tenant.second",
                resource_type="tenant",
                resource_id="tenant-a-second",
                result="success",
            )
            second.created_at = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    finally:
        await engine.dispose()


def _platform_token() -> str:
    return LocalJwtProvider(LocalJwtConfig(secret="test-secret")).issue_token(
        TokenClaims(
            user_id="auditor-1",
            session_id="sess-auditor",
            auth_provider="local",
            token_version=1,
            tenant_id=None,
        )
    )
