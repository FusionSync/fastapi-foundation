from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config.settings import DatabaseTenantFallbackMode, Settings
from core.db.transactions import UnitOfWork
from core.exceptions import AppError

DatabaseSessionIntent = Literal["write", "read"]


@dataclass(frozen=True, slots=True)
class DatabaseTenantFallback:
    mode: DatabaseTenantFallbackMode = "disabled"
    setting_name: str = "app.tenant_id"

    def __post_init__(self) -> None:
        if self.mode not in {"disabled", "session_variable"}:
            raise AppError(
                "VALIDATION_ERROR",
                "Database tenant fallback mode is invalid",
                status_code=400,
            )
        if not self.setting_name.strip():
            raise AppError(
                "VALIDATION_ERROR",
                "Database tenant fallback setting name is required",
                status_code=400,
            )

    async def apply(self, session: AsyncSession, tenant_id: str) -> None:
        if not tenant_id.strip():
            raise AppError(
                "VALIDATION_ERROR",
                "Database tenant fallback requires tenant_id",
                status_code=400,
            )
        session.info["tenant_id"] = tenant_id
        session.info["tenant_fallback"] = self.to_dict()
        if self.mode == "disabled":
            return
        if _session_dialect_name(session) != "postgresql":
            return
        await session.execute(
            text("select set_config(:setting_name, :tenant_id, true)"),
            {
                "setting_name": self.setting_name,
                "tenant_id": tenant_id,
            },
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "setting_name": self.setting_name,
        }


@dataclass(frozen=True, slots=True)
class DatabaseRuntimeDiagnostics:
    write_url: str
    write_pool: dict[str, object]
    read_url: str | None
    read_pool: dict[str, object] | None
    tenant_fallback: DatabaseTenantFallback

    def to_dict(self) -> dict[str, object]:
        return {
            "write": {
                "url": self.write_url,
                "pool": self.write_pool,
            },
            "read": {
                "configured": self.read_url is not None,
                "url": self.read_url,
                "pool": self.read_pool,
            },
            "tenant_fallback": self.tenant_fallback.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class DatabaseRuntime:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    read_engine: AsyncEngine | None = None
    read_session_factory: async_sessionmaker[AsyncSession] | None = None
    tenant_fallback: DatabaseTenantFallback = DatabaseTenantFallback()

    async def dispose(self) -> None:
        await self.engine.dispose()
        if self.read_engine is not None:
            await self.read_engine.dispose()

    def session_factory_for(
        self,
        intent: DatabaseSessionIntent = "write",
    ) -> async_sessionmaker[AsyncSession]:
        if intent == "write":
            return self.session_factory
        if intent == "read":
            return self.read_session_factory or self.session_factory
        raise AppError(
            "VALIDATION_ERROR",
            "Database session intent is invalid",
            status_code=400,
        )

    def unit_of_work(
        self,
        *,
        intent: DatabaseSessionIntent = "write",
        tenant_id: str | None = None,
    ) -> UnitOfWork:
        return UnitOfWork(
            self.session_factory_for(intent),
            tenant_fallback=self.tenant_fallback,
            tenant_id=tenant_id,
        )

    def diagnostics(self) -> DatabaseRuntimeDiagnostics:
        return DatabaseRuntimeDiagnostics(
            write_url=_safe_url(str(self.engine.url)),
            write_pool=_pool_diagnostics(self.engine),
            read_url=_safe_url(str(self.read_engine.url)) if self.read_engine is not None else None,
            read_pool=_pool_diagnostics(self.read_engine) if self.read_engine is not None else None,
            tenant_fallback=self.tenant_fallback,
        )


def create_database_runtime(settings: Settings) -> DatabaseRuntime:
    engine = create_async_engine(settings.database.url, **_engine_options(settings))
    read_engine = (
        create_async_engine(settings.database.read_url, **_engine_options(settings))
        if settings.database.read_url
        else None
    )
    read_session_factory = (
        async_sessionmaker(read_engine, expire_on_commit=False) if read_engine is not None else None
    )
    return DatabaseRuntime(
        engine=engine,
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        read_engine=read_engine,
        read_session_factory=read_session_factory,
        tenant_fallback=DatabaseTenantFallback(
            mode=settings.database.tenant_fallback_mode,
            setting_name=settings.database.tenant_fallback_setting_name,
        ),
    )


def _engine_options(settings: Settings) -> dict[str, int]:
    options: dict[str, int] = {}
    if settings.database.pool_size is not None:
        options["pool_size"] = settings.database.pool_size
    if settings.database.max_overflow is not None:
        options["max_overflow"] = settings.database.max_overflow
    return options


def _safe_url(url: str) -> str:
    return make_url(url).render_as_string(hide_password=True)


def _pool_diagnostics(engine: AsyncEngine) -> dict[str, object]:
    pool = engine.sync_engine.pool
    diagnostics: dict[str, object] = {"class": type(pool).__name__}
    for attribute in ("size", "checkedin", "checkedout", "overflow"):
        metric = getattr(pool, attribute, None)
        if not callable(metric):
            continue
        try:
            diagnostics[attribute] = metric()
        except (NotImplementedError, TypeError):
            continue
    status = getattr(pool, "status", None)
    if callable(status):
        diagnostics["status"] = status()
    return diagnostics


def _session_dialect_name(session: AsyncSession) -> str:
    bind = session.get_bind()
    return bind.dialect.name if bind is not None else ""
