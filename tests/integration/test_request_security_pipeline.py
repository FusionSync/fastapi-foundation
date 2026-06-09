import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.app import create_app
from core.auth import LocalJwtConfig, LocalJwtProvider, TokenClaims
from core.auth.request_security import DatabaseRequestSecurityPipeline
from core.base.models import BaseModel
from core.config import Settings
from core.permissions import PLATFORM_TENANT_ID, ProjectedPolicy
from core.tenancy import Tenant, TenantMember
from platform_apps.accounts import AccountsAuthSessionStore
from platform_apps.accounts.models import User, UserSession
from platform_apps.audit import AuditLog, AuditService


def test_request_security_pipeline_authenticates_tenant_and_route_permission(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'security-pipeline.db'}"
    asyncio.run(_seed_security_facts(database_url, include_policy=True))
    token = _token()
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_protected_runtime_app(tmp_path)

    session_factory = _session_factory(database_url)
    pipeline = DatabaseRequestSecurityPipeline(
        session_factory=session_factory,
        jwt_provider=LocalJwtProvider(LocalJwtConfig(secret="test-secret")),
        session_store_factory=AccountsAuthSessionStore,
    )
    app = create_app(
        Settings(
            database={"url": database_url},
            security={"jwt_secret": "test-secret"},
            installed_apps=["runtime_apps.secure_runtime.module"],
        ),
        request_security_pipeline=pipeline,
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/secure/ping",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["code"] == "OK"
    assert response.json()["data"] == {"name": "ok"}


def test_request_security_pipeline_rejects_missing_route_permission(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'security-pipeline-denied.db'}"
    asyncio.run(_seed_security_facts(database_url, include_policy=False))
    token = _token()
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_protected_runtime_app(tmp_path)

    session_factory = _session_factory(database_url)
    pipeline = DatabaseRequestSecurityPipeline(
        session_factory=session_factory,
        jwt_provider=LocalJwtProvider(LocalJwtConfig(secret="test-secret")),
        session_store_factory=AccountsAuthSessionStore,
    )
    app = create_app(
        Settings(
            database={"url": database_url},
            security={"jwt_secret": "test-secret"},
            installed_apps=["runtime_apps.secure_runtime.module"],
        ),
        request_security_pipeline=pipeline,
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/secure/ping",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


def test_request_security_pipeline_audits_route_permission_denial(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'security-pipeline-audit.db'}"
    asyncio.run(_seed_security_facts(database_url, include_policy=False))
    token = _token()
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_protected_runtime_app(tmp_path)

    session_factory = _session_factory(database_url)
    pipeline = DatabaseRequestSecurityPipeline(
        session_factory=session_factory,
        jwt_provider=LocalJwtProvider(LocalJwtConfig(secret="test-secret")),
        session_store_factory=AccountsAuthSessionStore,
        audit_factory=AuditService,
    )
    app = create_app(
        Settings(
            database={"url": database_url},
            security={"jwt_secret": "test-secret"},
            installed_apps=["runtime_apps.secure_runtime.module"],
        ),
        request_security_pipeline=pipeline,
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/secure/ping",
        headers={"Authorization": f"Bearer {token}"},
    )

    audit_logs = asyncio.run(_audit_logs(session_factory))
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    assert len(audit_logs) == 1
    assert audit_logs[0].action == "authorization.denied"
    assert audit_logs[0].result == "denied"
    assert audit_logs[0].tenant_id == "tenant-a"
    assert audit_logs[0].actor_id == "user-1"
    assert audit_logs[0].resource_type == "secure"
    assert audit_logs[0].request_id is not None
    assert audit_logs[0].payload == {
        "resource": "secure",
        "action": "read",
        "subject": "user:user-1",
        "reason": "missing_projected_policy",
    }


def test_request_security_pipeline_exposes_route_authorization_decision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'security-pipeline-decision.db'}"
    asyncio.run(_seed_security_facts(database_url, include_policy=True))
    token = _token()
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_protected_runtime_app(tmp_path, use_decision_dependency=True)

    session_factory = _session_factory(database_url)
    pipeline = DatabaseRequestSecurityPipeline(
        session_factory=session_factory,
        jwt_provider=LocalJwtProvider(LocalJwtConfig(secret="test-secret")),
        session_store_factory=AccountsAuthSessionStore,
    )
    app = create_app(
        Settings(
            database={"url": database_url},
            security={"jwt_secret": "test-secret"},
            installed_apps=["runtime_apps.secure_runtime.module"],
        ),
        request_security_pipeline=pipeline,
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/secure/mutate",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "name": "secure:read:tenant-a:user-1:1",
    }


def test_request_security_pipeline_rejects_header_tenant_selection_for_tenant_route(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'header-tenant-selection.db'}"
    asyncio.run(_seed_header_selection_facts(database_url))
    token = _auth_only_token()
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_protected_runtime_app(tmp_path)

    session_factory = _session_factory(database_url)
    pipeline = DatabaseRequestSecurityPipeline(
        session_factory=session_factory,
        jwt_provider=LocalJwtProvider(LocalJwtConfig(secret="test-secret")),
        session_store_factory=AccountsAuthSessionStore,
    )
    app = create_app(
        Settings(
            database={"url": database_url},
            security={"jwt_secret": "test-secret"},
            installed_apps=["runtime_apps.secure_runtime.module"],
        ),
        request_security_pipeline=pipeline,
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/secure/ping",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "tenant-a"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "TENANT_CONTEXT_CONFLICT"
    assert response.json()["details"] == {"reason": "header_tenant_not_allowed"}


def test_request_security_pipeline_allows_auth_without_tenant_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'auth-only.db'}"
    asyncio.run(_seed_auth_only_facts(database_url))
    token = _auth_only_token()
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_auth_only_runtime_app(tmp_path)

    session_factory = _session_factory(database_url)
    pipeline = DatabaseRequestSecurityPipeline(
        session_factory=session_factory,
        jwt_provider=LocalJwtProvider(LocalJwtConfig(secret="test-secret")),
        session_store_factory=AccountsAuthSessionStore,
    )
    app = create_app(
        Settings(
            database={"url": database_url},
            security={"jwt_secret": "test-secret"},
            installed_apps=["runtime_apps.auth_only_runtime.module"],
        ),
        request_security_pipeline=pipeline,
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/auth-only/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "tenant_id": None,
        "user_id": "user-1",
    }


def test_request_security_pipeline_authorizes_platform_scope_without_tenant_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-scope.db'}"
    asyncio.run(_seed_auth_only_facts(database_url, include_platform_policy=True))
    token = _auth_only_token()
    _purge_runtime_apps()
    monkeypatch.syspath_prepend(str(tmp_path))
    _write_auth_only_runtime_app(tmp_path, platform_permission=True)

    session_factory = _session_factory(database_url)
    pipeline = DatabaseRequestSecurityPipeline(
        session_factory=session_factory,
        jwt_provider=LocalJwtProvider(LocalJwtConfig(secret="test-secret")),
        session_store_factory=AccountsAuthSessionStore,
    )
    app = create_app(
        Settings(
            database={"url": database_url},
            security={"jwt_secret": "test-secret"},
            installed_apps=["runtime_apps.auth_only_runtime.module"],
        ),
        request_security_pipeline=pipeline,
    )
    client = TestClient(app)

    response = client.get(
        "/api/v1/auth-only/platform",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "action": "manage",
        "resource": "tenant",
        "tenant_id": PLATFORM_TENANT_ID,
        "user_id": "user-1",
    }


def _session_factory(database_url: str) -> async_sessionmaker:
    engine = create_async_engine(database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_security_facts(database_url: str, *, include_policy: bool) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                Tenant(
                    id="tenant-a",
                    name="Tenant A",
                    code="tenant-a",
                    status="active",
                    deployment_mode="local",
                )
            )
            session.add(TenantMember(tenant_id="tenant-a", user_id="user-1", status="active"))
            session.add(
                User(
                    id="user-1",
                    email="owner@example.com",
                    display_name="Owner",
                    status="active",
                    token_version=1,
                )
            )
            session.add(
                UserSession(
                    id="sess-1",
                    user_id="user-1",
                    tenant_id="tenant-a",
                    auth_provider="local",
                    status="active",
                    token_version=1,
                )
            )
            if include_policy:
                session.add(
                    ProjectedPolicy(
                        tenant_id="tenant-a",
                        subject="user:user-1",
                        resource="secure",
                        action="read",
                        effect="allow",
                        role_grant_id="grant-1",
                        policy_version=1,
                    )
                )
            await session.commit()
    finally:
        await engine.dispose()


async def _seed_auth_only_facts(
    database_url: str,
    *,
    include_platform_policy: bool = False,
) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                User(
                    id="user-1",
                    email="owner@example.com",
                    display_name="Owner",
                    status="active",
                    token_version=1,
                )
            )
            session.add(
                UserSession(
                    id="sess-auth-only",
                    user_id="user-1",
                    tenant_id=None,
                    auth_provider="local",
                    status="active",
                    token_version=1,
                )
            )
            if include_platform_policy:
                session.add(
                    ProjectedPolicy(
                        tenant_id=PLATFORM_TENANT_ID,
                        subject="user:user-1",
                        resource="tenant",
                        action="manage",
                        effect="allow",
                        role_grant_id="grant-platform",
                        policy_version=1,
                    )
                )
            await session.commit()
    finally:
        await engine.dispose()


async def _seed_header_selection_facts(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            session.add(
                Tenant(
                    id="tenant-a",
                    name="Tenant A",
                    code="tenant-a",
                    status="active",
                    deployment_mode="local",
                )
            )
            session.add(TenantMember(tenant_id="tenant-a", user_id="user-1", status="active"))
            session.add(
                User(
                    id="user-1",
                    email="owner@example.com",
                    display_name="Owner",
                    status="active",
                    token_version=1,
                )
            )
            session.add(
                UserSession(
                    id="sess-auth-only",
                    user_id="user-1",
                    tenant_id=None,
                    auth_provider="local",
                    status="active",
                    token_version=1,
                )
            )
            session.add(
                ProjectedPolicy(
                    tenant_id="tenant-a",
                    subject="user:user-1",
                    resource="secure",
                    action="read",
                    effect="allow",
                    role_grant_id="grant-tenant",
                    policy_version=1,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


def _token() -> str:
    return LocalJwtProvider(LocalJwtConfig(secret="test-secret")).issue_token(
        TokenClaims(
            user_id="user-1",
            session_id="sess-1",
            auth_provider="local",
            token_version=1,
            tenant_id="tenant-a",
        )
    )


def _auth_only_token() -> str:
    return LocalJwtProvider(LocalJwtConfig(secret="test-secret")).issue_token(
        TokenClaims(
            user_id="user-1",
            session_id="sess-auth-only",
            auth_provider="local",
            token_version=1,
            tenant_id=None,
        )
    )


async def _audit_logs(session_factory: async_sessionmaker) -> list[AuditLog]:
    async with session_factory() as session:
        result = await session.execute(select(AuditLog).order_by(AuditLog.created_at))
        audit_logs = list(result.scalars().all())
        for audit_log in audit_logs:
            session.expunge(audit_log)
        return audit_logs


def _write_protected_runtime_app(root: Path, *, use_decision_dependency: bool = False) -> None:
    app_dir = root / "runtime_apps" / "secure_runtime"
    migrations_dir = app_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    (root / "runtime_apps" / "__init__.py").touch()
    _write(app_dir / "__init__.py", "from runtime_apps.secure_runtime.module import module\n")
    _write(
        app_dir / "schemas.py",
        "from core.base import BaseSchema\n\nclass RuntimeSchema(BaseSchema):\n    name: str\n",
    )
    _write(app_dir / "models.py", "MODEL_IMPORTED = True\n")
    _write(app_dir / "services.py", "class RuntimeService:\n    pass\n")
    if use_decision_dependency:
        router_source = (
            "from typing import Annotated\n"
            "from fastapi import Depends\n"
            "from runtime_apps.secure_runtime.schemas import RuntimeSchema\n"
            "from core.base import create_router\n"
            "from core.permissions import AuthorizationDecision, route_authorization_decision\n"
            "from core.serialization import Envelope, ok\n\n"
            "router = create_router('/secure', permissions=['secure:read'])\n\n"
            "@router.post('/mutate', response_model=Envelope[RuntimeSchema])\n"
            "async def mutate(\n"
            "    decision: Annotated[\n"
            "        AuthorizationDecision,\n"
            "        Depends(route_authorization_decision),\n"
            "    ],\n"
            "):\n"
            "    return ok(\n"
            "        {\n"
            "            'name': (\n"
            "                f'{decision.resource}:{decision.action}:'\n"
            "                f'{decision.tenant_id}:{decision.user_id}:'\n"
            "                f'{decision.policy_version}'\n"
            "            )\n"
            "        }\n"
            "    )\n"
        )
    else:
        router_source = (
            "from runtime_apps.secure_runtime.schemas import RuntimeSchema\n"
            "from core.base import create_router\n"
            "from core.serialization import Envelope, ok\n\n"
            "router = create_router('/secure', permissions=['secure:read'])\n\n"
            "@router.get('/ping', response_model=Envelope[RuntimeSchema])\n"
            "async def ping():\n"
            "    return ok({'name': 'ok'})\n"
        )
    _write(app_dir / "router.py", router_source)
    _write(
        app_dir / "permissions.py",
        "from core.permissions import PermissionSpec\n\n"
        "PERMISSIONS = [PermissionSpec(resource='secure', action='read')]\n",
    )
    _write(migrations_dir / "__init__.py", "")
    _write(migrations_dir / "manifest.py", "MIGRATIONS = []\n")
    _write(
        app_dir / "module.py",
        "from runtime_apps.secure_runtime.permissions import PERMISSIONS\n"
        "from runtime_apps.secure_runtime.router import router\n"
        "from core.apps import AppModule, MigrationSpec\n\n"
        "module = AppModule(\n"
        "    label='secure_runtime',\n"
        "    version='0.1.0',\n"
        "    routers=[router],\n"
        "    models=['runtime_apps.secure_runtime.models'],\n"
        "    migrations=MigrationSpec(path='runtime_apps.secure_runtime.migrations'),\n"
        "    permissions=PERMISSIONS,\n"
        ")\n",
    )


def _write_auth_only_runtime_app(
    root: Path,
    *,
    platform_permission: bool = False,
) -> None:
    app_dir = root / "runtime_apps" / "auth_only_runtime"
    migrations_dir = app_dir / "migrations"
    migrations_dir.mkdir(parents=True)
    (root / "runtime_apps" / "__init__.py").touch()
    _write(app_dir / "__init__.py", "from runtime_apps.auth_only_runtime.module import module\n")
    _write(
        app_dir / "schemas.py",
        "from core.base import BaseSchema\n\nclass RuntimeIdentitySchema(BaseSchema):\n"
        "    user_id: str\n"
        "    tenant_id: str | None\n"
        "\n\nclass RuntimeDecisionSchema(RuntimeIdentitySchema):\n"
        "    resource: str | None = None\n"
        "    action: str | None = None\n",
    )
    _write(app_dir / "models.py", "MODEL_IMPORTED = True\n")
    _write(app_dir / "services.py", "class RuntimeService:\n    pass\n")
    platform_route = (
        "platform_router = create_router(\n"
        "    '/auth-only',\n"
        "    tenant_required=False,\n"
        "    permissions=['tenant:manage'],\n"
        "    permission_scope='platform',\n"
        ")\n\n"
        "@platform_router.get('/platform', response_model=Envelope[RuntimeDecisionSchema])\n"
        "async def platform(\n"
        "    decision: Annotated[\n"
        "        AuthorizationDecision,\n"
        "        Depends(route_authorization_decision),\n"
        "    ],\n"
        "):\n"
        "    return ok(\n"
        "        {\n"
        "            'user_id': decision.user_id,\n"
        "            'tenant_id': decision.tenant_id,\n"
        "            'resource': decision.resource,\n"
        "            'action': decision.action,\n"
        "        }\n"
        "    )\n"
        if platform_permission
        else ""
    )
    routers = "[router, platform_router]" if platform_permission else "[router]"
    _write(
        app_dir / "router.py",
        "from typing import Annotated\n"
        "from fastapi import Depends\n"
        "from core.base import create_router\n"
        "from core.context import get_current_context\n"
        "from core.permissions import AuthorizationDecision, route_authorization_decision\n"
        "from core.serialization import Envelope, ok\n"
        "from runtime_apps.auth_only_runtime.schemas import (\n"
        "    RuntimeDecisionSchema,\n"
        "    RuntimeIdentitySchema,\n"
        ")\n\n"
        "router = create_router('/auth-only', tenant_required=False)\n\n"
        "@router.get('/me', response_model=Envelope[RuntimeIdentitySchema])\n"
        "async def me():\n"
        "    context = get_current_context()\n"
        "    return ok({'user_id': context.user_id, 'tenant_id': context.tenant_id})\n\n"
        f"{platform_route}",
    )
    _write(
        app_dir / "permissions.py",
        "from core.permissions import PermissionSpec\n\n"
        "PERMISSIONS = [PermissionSpec(resource='tenant', action='manage', scope='platform')]\n",
    )
    _write(migrations_dir / "__init__.py", "")
    _write(migrations_dir / "manifest.py", "MIGRATIONS = []\n")
    _write(
        app_dir / "module.py",
        "from runtime_apps.auth_only_runtime.permissions import PERMISSIONS\n"
        f"from runtime_apps.auth_only_runtime.router import {routers[1:-1]}\n"
        "from core.apps import AppModule, MigrationSpec\n\n"
        "module = AppModule(\n"
        "    label='auth_only_runtime',\n"
        "    version='0.1.0',\n"
        f"    routers={routers},\n"
        "    models=['runtime_apps.auth_only_runtime.models'],\n"
        "    migrations=MigrationSpec(path='runtime_apps.auth_only_runtime.migrations'),\n"
        "    permissions=PERMISSIONS,\n"
        ")\n",
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _purge_runtime_apps() -> None:
    for name in list(sys.modules):
        if name == "runtime_apps" or name.startswith("runtime_apps."):
            del sys.modules[name]
