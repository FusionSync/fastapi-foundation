import json
from pathlib import Path

from core.cli.main import main


def test_config_write_artifacts_writes_dockerfile_and_compose_bundle(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = main(
        [
            "config",
            "write-artifacts",
            "--profile",
            "private",
            "--target",
            "docker-compose",
            "--output-dir",
            str(tmp_path),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["command"] == "config write-artifacts"
    assert sorted(payload["written_files"]) == [
        "Dockerfile",
        "docker-compose.private.yml",
    ]
    assert (tmp_path / "Dockerfile").read_text(encoding="utf-8").startswith("FROM python:")
    compose = (tmp_path / "docker-compose.private.yml").read_text(encoding="utf-8")
    assert "server:" in compose
    assert "worker:" in compose


def test_config_write_artifacts_writes_helm_chart_bundle(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "config",
            "write-artifacts",
            "--profile",
            "cloud",
            "--target",
            "helm-values",
            "--output-dir",
            str(tmp_path),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert sorted(payload["written_files"]) == [
        "helm/fastapi-foundation/Chart.yaml",
        "helm/fastapi-foundation/templates/workload.yaml",
        "helm/fastapi-foundation/values.cloud.yaml",
    ]
    assert "apiVersion: v2" in (
        tmp_path / "helm" / "fastapi-foundation" / "Chart.yaml"
    ).read_text(encoding="utf-8")
    assert "workloads:" in (
        tmp_path / "helm" / "fastapi-foundation" / "values.cloud.yaml"
    ).read_text(encoding="utf-8")
