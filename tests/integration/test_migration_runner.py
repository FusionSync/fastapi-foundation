import json
import sys
import types
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from core.apps import AppModule, MigrationSpec
from core.cli.main import main
from core.locks import MemoryLockProvider
from core.migrations import (
    AlembicMigrationExecutor,
    MigrationExecutorResult,
    MigrationManifest,
    apply_migrations,
    run_preflight,
)


@pytest.mark.asyncio
async def test_apply_migrations_runs_exact_manifest_revisions_under_lock() -> None:
    manifest = _manifest("0001_initial", "alpha_0001_initial")
    executor = RecordingMigrationExecutor(["alpha_0001_initial"])
    locks = MemoryLockProvider()

    result = await apply_migrations(
        run_preflight([manifest]),
        executor=executor,
        lock_provider=locks,
        owner_token="migrator-1",
    )

    assert result.ok is True
    assert result.applied is True
    assert result.mode == "executor"
    assert executor.calls == [["alpha_0001_initial"]]
    assert await locks.locked("migrations:apply") is False


@pytest.mark.asyncio
async def test_apply_migrations_does_not_run_executor_without_migration_lock() -> None:
    manifest = _manifest("0001_initial", "alpha_0001_initial")
    executor = RecordingMigrationExecutor(["alpha_0001_initial"])
    locks = MemoryLockProvider()
    await locks.acquire("migrations:apply", owner_token="other-migrator", ttl_seconds=60)

    result = await apply_migrations(
        run_preflight([manifest]),
        executor=executor,
        lock_provider=locks,
        owner_token="migrator-1",
    )

    assert result.ok is False
    assert result.applied is False
    assert result.mode == "executor"
    assert executor.calls == []
    assert any("LOCK_NOT_ACQUIRED" in error for error in result.errors)
    assert await locks.locked("migrations:apply") is True


@pytest.mark.asyncio
async def test_apply_migrations_rejects_executor_revision_mismatch() -> None:
    manifest = _manifest("0001_initial", "alpha_0001_initial")
    executor = RecordingMigrationExecutor(["wrong_revision"])
    locks = MemoryLockProvider()

    result = await apply_migrations(
        run_preflight([manifest]),
        executor=executor,
        lock_provider=locks,
        owner_token="migrator-1",
    )

    assert result.ok is False
    assert result.applied is False
    assert result.mode == "executor"
    assert executor.calls == [["alpha_0001_initial"]]
    assert "migration executor revision mismatch" in result.errors
    assert await locks.locked("migrations:apply") is False


@pytest.mark.asyncio
async def test_alembic_migration_executor_applies_revision_and_verifies_database_head(
    tmp_path: Path,
) -> None:
    db_url, config_path = _write_minimal_alembic_project(
        tmp_path,
        revision="alpha_0001_initial",
    )
    manifest = _manifest("0001_initial", "alpha_0001_initial")
    locks = MemoryLockProvider()

    result = await apply_migrations(
        run_preflight([manifest]),
        executor=AlembicMigrationExecutor(
            config_path=config_path,
            database_url=db_url,
        ),
        lock_provider=locks,
        owner_token="migrator-1",
    )

    engine = create_engine(db_url)
    try:
        tables = inspect(engine).get_table_names()
    finally:
        engine.dispose()

    assert result.ok is True
    assert result.applied is True
    assert result.mode == "executor"
    assert "alpha_items" in tables


def test_migrate_apply_cli_runs_alembic_executor_when_config_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    db_url, config_path = _write_minimal_alembic_project(
        tmp_path,
        revision="alpha_0001_initial",
    )
    _install_app(
        monkeypatch,
        "fake_alpha",
        label="alpha",
        manifests=[_manifest("0001_initial", "alpha_0001_initial")],
    )

    exit_code = main(
        [
            "migrate",
            "apply",
            "--installed-app",
            "fake_alpha",
            "--alembic-config",
            str(config_path),
            "--database-url",
            db_url,
            "--yes",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    engine = create_engine(db_url)
    try:
        tables = inspect(engine).get_table_names()
    finally:
        engine.dispose()

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["applied"] is True
    assert payload["mode"] == "executor"
    assert payload["migrations"][0]["alembic_revision"] == "alpha_0001_initial"
    assert "alpha_items" in tables


class RecordingMigrationExecutor:
    def __init__(self, applied_revisions: list[str]) -> None:
        self.applied_revisions = applied_revisions
        self.calls: list[list[str]] = []

    async def apply(
        self,
        migrations: Sequence[MigrationManifest],
    ) -> MigrationExecutorResult:
        self.calls.append([migration.alembic_revision or "" for migration in migrations])
        return MigrationExecutorResult(
            ok=True,
            applied_revisions=self.applied_revisions,
        )


def _manifest(migration_id: str, revision: str) -> MigrationManifest:
    return MigrationManifest(
        app_label="alpha",
        migration_id=migration_id,
        alembic_revision=revision,
        phase="expand",
        classification="reversible",
    )


def _write_minimal_alembic_project(
    tmp_path: Path,
    *,
    revision: str,
) -> tuple[str, Path]:
    script_dir = tmp_path / "alembic"
    versions_dir = script_dir / "versions"
    versions_dir.mkdir(parents=True)
    db_path = tmp_path / "app.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    config_path = tmp_path / "alembic.ini"
    config_path.write_text(
        "\n".join(
            [
                "[alembic]",
                f"script_location = {script_dir.as_posix()}",
                f"sqlalchemy.url = {db_url}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (script_dir / "env.py").write_text(
        """
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
target_metadata = None


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
""".lstrip(),
        encoding="utf-8",
    )
    (versions_dir / f"{revision}.py").write_text(
        f'''
from alembic import op
import sqlalchemy as sa

revision = "{revision}"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("alpha_items", sa.Column("id", sa.Integer(), primary_key=True))


def downgrade():
    op.drop_table("alpha_items")
'''.lstrip(),
        encoding="utf-8",
    )
    return db_url, config_path


def _install_app(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    *,
    label: str,
    manifests: list[MigrationManifest],
) -> None:
    app_module = types.ModuleType(module_path)
    migrations_path = f"{module_path}_migrations"
    app_module.module = AppModule(
        label=label,
        version="0.1.0",
        migrations=MigrationSpec(path=migrations_path),
    )
    migrations_package = types.ModuleType(migrations_path)
    migrations_package.__path__ = []
    manifest_module = types.ModuleType(f"{migrations_path}.manifest")
    manifest_module.MIGRATIONS = manifests
    monkeypatch.setitem(sys.modules, module_path, app_module)
    monkeypatch.setitem(sys.modules, migrations_path, migrations_package)
    monkeypatch.setitem(sys.modules, f"{migrations_path}.manifest", manifest_module)
