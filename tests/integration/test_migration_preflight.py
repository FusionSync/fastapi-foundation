from core.migrations import MigrationManifest, check_drift, run_preflight


def test_preflight_blocks_destructive_migration_without_approval() -> None:
    manifest = MigrationManifest(
        app_label="alpha",
        migration_id="0002_drop_legacy",
        alembic_revision="alpha_0002_drop_legacy",
        phase="contract",
        classification="destructive",
        destructive_operations=["drop column legacy_name"],
    )

    result = run_preflight([manifest], backup_ready=True)

    assert result.ok is False
    assert any("requires approved_by and approved_at" in error for error in result.errors)
    assert any("requires rollback_strategy" in error for error in result.errors)


def test_preflight_blocks_destructive_migration_without_backup_readiness() -> None:
    manifest = MigrationManifest(
        app_label="alpha",
        migration_id="0002_drop_legacy",
        alembic_revision="alpha_0002_drop_legacy",
        phase="contract",
        classification="destructive",
        destructive_operations=["drop column legacy_name"],
        approved_by="dba",
        approved_at="2026-05-28T00:00:00Z",
        rollback_strategy="restore backup or forward-fix",
    )

    result = run_preflight([manifest], backup_ready=False)

    assert result.ok is False
    assert "alpha:0002_drop_legacy requires backup readiness before execution" in result.errors


def test_preflight_allows_approved_destructive_migration_with_backup_readiness() -> None:
    manifest = MigrationManifest(
        app_label="alpha",
        migration_id="0002_drop_legacy",
        alembic_revision="alpha_0002_drop_legacy",
        phase="contract",
        classification="destructive",
        destructive_operations=["drop column legacy_name"],
        approved_by="dba",
        approved_at="2026-05-28T00:00:00Z",
        rollback_strategy="restore backup or forward-fix",
    )

    result = run_preflight([manifest], backup_ready=True)

    assert result.ok is True
    assert result.errors == []


def test_preflight_blocks_schema_drift() -> None:
    manifest = MigrationManifest(
        app_label="alpha",
        migration_id="0001_initial",
        alembic_revision="alpha_0001_initial",
        phase="expand",
        classification="reversible",
    )
    drift_report = check_drift(
        expected_heads={"alpha": "0001_initial"},
        actual_heads={"alpha": "0000_unknown"},
    )

    result = run_preflight([manifest], drift_report=drift_report)

    assert result.ok is False
    assert "schema drift: alpha: expected '0001_initial', actual '0000_unknown'" in result.errors


def test_preflight_blocks_high_lock_risk_without_backfill_plan() -> None:
    manifest = MigrationManifest(
        app_label="alpha",
        migration_id="0003_add_not_null",
        alembic_revision="alpha_0003_add_not_null",
        phase="expand",
        classification="reversible",
        estimated_rows=1_000_000,
        lock_risk="high",
    )

    result = run_preflight([manifest])

    assert result.ok is False
    assert any("must declare backfill_plan" in error for error in result.errors)
