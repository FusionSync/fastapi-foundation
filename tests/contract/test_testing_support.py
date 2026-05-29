from core.apps.conformance import check_app
from core.testing import (
    build_prerelease_checklist,
    create_business_app_fixture,
    create_tenant_user_fixture,
)


def test_business_app_fixture_generates_conformance_ready_app(tmp_path, monkeypatch) -> None:
    fixture = create_business_app_fixture("fixture_sales_ops", target_root=tmp_path / "src")
    monkeypatch.syspath_prepend(str(fixture.target_root))

    result = check_app(fixture.module_path)

    assert fixture.label == "fixture_sales_ops"
    assert fixture.module_path == "test_apps.fixture_sales_ops.module"
    assert fixture.settings.installed_apps == ["test_apps.fixture_sales_ops.module"]
    assert "test_apps/fixture_sales_ops/module.py" in fixture.files
    assert "test_apps/fixture_sales_ops/tests/test_fixture_sales_ops_contract.py" in fixture.files
    assert fixture.check_app_command == (
        "core check-app test_apps.fixture_sales_ops.module --json"
    )
    assert result.ok is True


def test_tenant_user_fixture_returns_auth_tenancy_and_context_objects() -> None:
    fixture = create_tenant_user_fixture(
        tenant_id="tenant-a",
        user_id="user-1",
        email="owner@example.com",
    )

    assert fixture.tenant.id == "tenant-a"
    assert fixture.tenant.status == "active"
    assert fixture.member.tenant_id == "tenant-a"
    assert fixture.member.user_id == "user-1"
    assert fixture.auth_user.id == "user-1"
    assert fixture.auth_user.tenant_id == "tenant-a"
    assert fixture.tenancy_user.default_tenant_id == "tenant-a"
    assert fixture.tenancy_user.memberships[0].tenant_id == "tenant-a"
    assert fixture.request_context.tenant_id == "tenant-a"
    assert fixture.request_context.user_id == "user-1"


def test_prerelease_checklist_includes_required_gates_for_profile_and_apps() -> None:
    checklist = build_prerelease_checklist(
        profile="cloud",
        artifact_target="helm-values",
        installed_apps=["apps.example_domain.module"],
    )

    commands = [item.command for item in checklist.items]

    assert checklist.profile == "cloud"
    assert checklist.artifact_target == "helm-values"
    assert commands == [
        "uv run ruff check .",
        "uv run pytest -q",
        "git diff --check",
        "core check-app --all --installed-app apps.example_domain.module --json",
        "core permissions catalog --installed-app apps.example_domain.module --json",
        "core migrate plan --installed-app apps.example_domain.module --json",
        "core release checkpoint --profile cloud --artifact-target helm-values --json",
        (
            "core release checkpoint --profile cloud --artifact-target helm-values "
            "--probe-dependencies --json"
        ),
    ]
    assert all(item.required for item in checklist.items)
    assert checklist.to_dict()["items"][0]["evidence"] == "ruff exits 0"
