import json
import sys
import types

from core.apps import AppModule, AppRegistry, MigrationSpec
from core.cli.main import main
from core.migrations import MigrationManifest, MigrationRegistry, plan_migrations


def test_migration_registry_collects_app_manifests(monkeypatch) -> None:
    _install_app(
        monkeypatch,
        "fake_alpha",
        label="alpha",
        manifests=[
            MigrationManifest(
                app_label="alpha",
                migration_id="0001_initial",
                alembic_revision="alpha_0001_initial",
                phase="expand",
                classification="reversible",
            )
        ],
    )

    app_registry = AppRegistry(["fake_alpha"]).load()
    migration_registry = MigrationRegistry.from_app_registry(app_registry)

    assert migration_registry.errors == []
    assert [manifest.key for manifest in migration_registry.manifests] == ["alpha:0001_initial"]


def test_migration_plan_sorts_by_app_dependencies(monkeypatch) -> None:
    _install_app(
        monkeypatch,
        "fake_alpha",
        label="alpha",
        manifests=[
            MigrationManifest(
                app_label="alpha",
                migration_id="0001_initial",
                alembic_revision="alpha_0001_initial",
                phase="expand",
                classification="reversible",
            )
        ],
    )
    _install_app(
        monkeypatch,
        "fake_beta",
        label="beta",
        dependencies=["alpha"],
        manifests=[
            MigrationManifest(
                app_label="beta",
                migration_id="0001_initial",
                alembic_revision="beta_0001_initial",
                phase="expand",
                classification="reversible",
            )
        ],
    )

    app_registry = AppRegistry(["fake_alpha", "fake_beta"]).load()
    migration_registry = MigrationRegistry.from_app_registry(app_registry)
    plan = plan_migrations(migration_registry.manifests, app_registry=app_registry)

    assert plan.ok is True
    assert [manifest.key for manifest in plan.migrations] == [
        "alpha:0001_initial",
        "beta:0001_initial",
    ]


def test_migration_plan_rejects_missing_dependency() -> None:
    plan = plan_migrations(
        [
            MigrationManifest(
                app_label="alpha",
                migration_id="0002_next",
                alembic_revision="alpha_0002_next",
                phase="expand",
                classification="reversible",
                depends_on=["0001_initial"],
            )
        ]
    )

    assert plan.ok is False
    assert "alpha:0002_next depends on missing migration alpha:0001_initial" in plan.errors


def test_migration_plan_rejects_circular_dependency() -> None:
    plan = plan_migrations(
        [
            MigrationManifest(
                app_label="alpha",
                migration_id="0001_initial",
                alembic_revision="alpha_0001_initial",
                phase="expand",
                classification="reversible",
                depends_on=["0002_next"],
            ),
            MigrationManifest(
                app_label="alpha",
                migration_id="0002_next",
                alembic_revision="alpha_0002_next",
                phase="expand",
                classification="reversible",
                depends_on=["0001_initial"],
            ),
        ]
    )

    assert plan.ok is False
    assert any("Circular migration dependency" in error for error in plan.errors)


def test_migration_manifest_requires_alembic_revision_binding() -> None:
    manifest = MigrationManifest(
        app_label="alpha",
        migration_id="0001_initial",
        phase="expand",
        classification="reversible",
    )

    assert "alpha:0001_initial requires alembic_revision" in manifest.validate()


def test_migrate_plan_cli_outputs_stable_json(monkeypatch, capsys) -> None:
    _install_app(
        monkeypatch,
        "fake_alpha",
        label="alpha",
        manifests=[
            MigrationManifest(
                app_label="alpha",
                migration_id="0001_initial",
                alembic_revision="alpha_0001_initial",
                phase="expand",
                classification="reversible",
            )
        ],
    )

    exit_code = main(["migrate", "plan", "--installed-app", "fake_alpha", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["migrations"][0]["key"] == "alpha:0001_initial"
    assert payload["migrations"][0]["alembic_revision"] == "alpha_0001_initial"


def test_migrate_drift_check_cli_blocks_mismatch(capsys) -> None:
    exit_code = main(
        [
            "migrate",
            "drift-check",
            "--expected",
            "alpha=0001_initial",
            "--actual",
            "alpha=0000_unknown",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["drift"]["has_drift"] is True


def test_migrate_apply_requires_yes(monkeypatch, capsys) -> None:
    _install_app(
        monkeypatch,
        "fake_alpha",
        label="alpha",
        manifests=[
            MigrationManifest(
                app_label="alpha",
                migration_id="0001_initial",
                alembic_revision="alpha_0001_initial",
                phase="expand",
                classification="reversible",
            )
        ],
    )

    exit_code = main(["migrate", "apply", "--installed-app", "fake_alpha", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {
        "ok": False,
        "error": "migrate apply requires --yes",
    }


def test_migrate_apply_runs_preflight_before_metadata_apply(monkeypatch, capsys) -> None:
    _install_app(
        monkeypatch,
        "fake_alpha",
        label="alpha",
        manifests=[
            MigrationManifest(
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
        ],
    )

    exit_code = main(
        [
            "migrate",
            "apply",
            "--installed-app",
            "fake_alpha",
            "--yes",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["applied"] is False
    assert "alpha:0002_drop_legacy requires backup readiness before execution" in payload["errors"]


def test_migrate_apply_outputs_metadata_apply_plan_when_gates_pass(monkeypatch, capsys) -> None:
    _install_app(
        monkeypatch,
        "fake_alpha",
        label="alpha",
        manifests=[
            MigrationManifest(
                app_label="alpha",
                migration_id="0001_initial",
                alembic_revision="alpha_0001_initial",
                phase="expand",
                classification="reversible",
            ),
            MigrationManifest(
                app_label="alpha",
                migration_id="0002_drop_legacy",
                alembic_revision="alpha_0002_drop_legacy",
                phase="contract",
                classification="destructive",
                depends_on=["0001_initial"],
                destructive_operations=["drop column legacy_name"],
                approved_by="dba",
                approved_at="2026-05-28T00:00:00Z",
                rollback_strategy="restore backup or forward-fix",
            ),
        ],
    )

    exit_code = main(
        [
            "migrate",
            "apply",
            "--installed-app",
            "fake_alpha",
            "--backup-ready",
            "--yes",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["applied"] is True
    assert payload["mode"] == "metadata"
    assert [item["key"] for item in payload["migrations"]] == [
        "alpha:0001_initial",
        "alpha:0002_drop_legacy",
    ]


def _install_app(
    monkeypatch,
    module_path: str,
    *,
    label: str,
    manifests: list[MigrationManifest],
    dependencies: list[str] | None = None,
) -> None:
    app_module = types.ModuleType(module_path)
    migrations_path = f"{module_path}_migrations"
    app_module.module = AppModule(
        label=label,
        version="0.1.0",
        dependencies=dependencies or [],
        migrations=MigrationSpec(path=migrations_path),
    )
    migrations_package = types.ModuleType(migrations_path)
    migrations_package.__path__ = []
    manifest_module = types.ModuleType(f"{migrations_path}.manifest")
    manifest_module.MIGRATIONS = manifests
    monkeypatch.setitem(sys.modules, module_path, app_module)
    monkeypatch.setitem(sys.modules, migrations_path, migrations_package)
    monkeypatch.setitem(sys.modules, f"{migrations_path}.manifest", manifest_module)
