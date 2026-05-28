from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from core.exceptions import AppError
from core.locks import LockProvider
from core.migrations.manifest import MigrationManifest
from core.migrations.preflight import PreflightResult

METADATA_APPLY_DISABLED_ERROR = (
    "migrate apply requires a real migration executor; metadata mode does not change schema"
)


@dataclass(frozen=True, slots=True)
class MigrationApplyResult:
    ok: bool
    applied: bool
    mode: str = "metadata"
    migrations: list[MigrationManifest] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "applied": self.applied,
            "mode": self.mode,
            "migrations": [manifest.to_dict() for manifest in self.migrations],
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass(frozen=True, slots=True)
class MigrationExecutorResult:
    ok: bool
    applied_revisions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class MigrationExecutor(Protocol):
    async def apply(
        self,
        migrations: Sequence[MigrationManifest],
    ) -> MigrationExecutorResult: ...


async def apply_migrations(
    preflight: PreflightResult,
    *,
    executor: MigrationExecutor,
    lock_provider: LockProvider,
    owner_token: str,
    lock_key: str = "migrations:apply",
    lock_ttl_seconds: int = 300,
) -> MigrationApplyResult:
    if not preflight.ok or preflight.plan is None:
        return MigrationApplyResult(
            ok=False,
            applied=False,
            mode="executor",
            migrations=preflight.plan.migrations if preflight.plan else [],
            errors=preflight.errors,
            warnings=preflight.warnings,
        )

    lock_acquired = False
    try:
        await lock_provider.require_acquire(
            lock_key,
            ttl_seconds=lock_ttl_seconds,
            owner_token=owner_token,
        )
        lock_acquired = True
        executor_result = await executor.apply(preflight.plan.migrations)
        expected_revisions = [
            migration.alembic_revision or "" for migration in preflight.plan.migrations
        ]
        warnings = [*preflight.warnings, *executor_result.warnings]
        if not executor_result.ok:
            return MigrationApplyResult(
                ok=False,
                applied=False,
                mode="executor",
                migrations=preflight.plan.migrations,
                errors=executor_result.errors,
                warnings=warnings,
            )
        if executor_result.applied_revisions != expected_revisions:
            return MigrationApplyResult(
                ok=False,
                applied=False,
                mode="executor",
                migrations=preflight.plan.migrations,
                errors=["migration executor revision mismatch"],
                warnings=warnings,
            )
        return MigrationApplyResult(
            ok=True,
            applied=True,
            mode="executor",
            migrations=preflight.plan.migrations,
            warnings=warnings,
        )
    except AppError as exc:
        return MigrationApplyResult(
            ok=False,
            applied=False,
            mode="executor",
            migrations=preflight.plan.migrations,
            errors=[f"{exc.code}: {exc.message}"],
            warnings=preflight.warnings,
        )
    except Exception as exc:
        return MigrationApplyResult(
            ok=False,
            applied=False,
            mode="executor",
            migrations=preflight.plan.migrations,
            errors=[f"{type(exc).__name__}: {exc}"],
            warnings=preflight.warnings,
        )
    finally:
        if lock_acquired:
            await lock_provider.release(lock_key, owner_token=owner_token)


def apply_migration_metadata(
    preflight: PreflightResult,
) -> MigrationApplyResult:
    if not preflight.ok or preflight.plan is None:
        return MigrationApplyResult(
            ok=False,
            applied=False,
            mode="metadata-apply-disabled",
            migrations=preflight.plan.migrations if preflight.plan else [],
            errors=preflight.errors,
            warnings=preflight.warnings,
        )
    return MigrationApplyResult(
        ok=False,
        applied=False,
        mode="metadata-apply-disabled",
        migrations=preflight.plan.migrations,
        errors=[METADATA_APPLY_DISABLED_ERROR],
        warnings=preflight.warnings,
    )


def dry_run_migration_metadata(
    preflight: PreflightResult,
) -> MigrationApplyResult:
    if not preflight.ok or preflight.plan is None:
        return MigrationApplyResult(
            ok=False,
            applied=False,
            mode="metadata-dry-run",
            migrations=preflight.plan.migrations if preflight.plan else [],
            errors=preflight.errors,
            warnings=preflight.warnings,
        )
    return MigrationApplyResult(
        ok=True,
        applied=False,
        mode="metadata-dry-run",
        migrations=preflight.plan.migrations,
        errors=[],
        warnings=preflight.warnings,
    )
