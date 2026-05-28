from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from os import PathLike

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from core.migrations.manifest import MigrationManifest
from core.migrations.runner import MigrationExecutorResult


@dataclass(frozen=True, slots=True)
class AlembicMigrationExecutor:
    config_path: str | PathLike[str]
    database_url: str | None = None
    script_location: str | PathLike[str] | None = None

    async def apply(
        self,
        migrations: Sequence[MigrationManifest],
    ) -> MigrationExecutorResult:
        return await asyncio.to_thread(self._apply_sync, list(migrations))

    def _apply_sync(self, migrations: list[MigrationManifest]) -> MigrationExecutorResult:
        config = self._build_config()
        applied_revisions: list[str] = []
        for migration in migrations:
            revision = migration.alembic_revision
            if not revision:
                return MigrationExecutorResult(
                    ok=False,
                    applied_revisions=applied_revisions,
                    errors=[f"{migration.key} requires alembic_revision"],
                )
            try:
                command.upgrade(config, revision)
                current_heads = _current_heads(config)
            except Exception as exc:
                return MigrationExecutorResult(
                    ok=False,
                    applied_revisions=applied_revisions,
                    errors=[f"{type(exc).__name__}: {exc}"],
                )
            if revision not in current_heads:
                return MigrationExecutorResult(
                    ok=False,
                    applied_revisions=applied_revisions,
                    errors=[
                        "alembic head verification failed for "
                        f"{migration.key}: expected {revision!r}, current heads {current_heads!r}"
                    ],
                )
            applied_revisions.append(revision)
        return MigrationExecutorResult(ok=True, applied_revisions=applied_revisions)

    def _build_config(self) -> Config:
        config = Config(str(self.config_path))
        if self.database_url:
            config.set_main_option("sqlalchemy.url", self.database_url)
        if self.script_location:
            config.set_main_option("script_location", str(self.script_location))
        return config


def _current_heads(config: Config) -> list[str]:
    database_url = config.get_main_option("sqlalchemy.url")
    if not database_url:
        raise ValueError("Alembic config requires sqlalchemy.url")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            return list(context.get_current_heads())
    finally:
        engine.dispose()
