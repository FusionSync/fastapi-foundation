from collections.abc import Sequence

import pytest

from core.locks import MemoryLockProvider
from core.migrations import (
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
