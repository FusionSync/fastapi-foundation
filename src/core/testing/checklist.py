from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.config.settings import DeploymentMode

ArtifactTarget = Literal["docker-compose", "systemd", "helm-values"]


@dataclass(frozen=True, slots=True)
class PrereleaseChecklistItem:
    name: str
    command: str
    evidence: str
    required: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "command": self.command,
            "evidence": self.evidence,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class PrereleaseChecklist:
    profile: DeploymentMode
    artifact_target: ArtifactTarget
    installed_apps: tuple[str, ...]
    items: tuple[PrereleaseChecklistItem, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "artifact_target": self.artifact_target,
            "installed_apps": list(self.installed_apps),
            "items": [item.to_dict() for item in self.items],
        }


def build_prerelease_checklist(
    *,
    profile: DeploymentMode = "local",
    artifact_target: ArtifactTarget = "docker-compose",
    installed_apps: list[str] | tuple[str, ...] = (),
) -> PrereleaseChecklist:
    app_args = _installed_app_args(installed_apps)
    release_command = (
        f"core release checkpoint --profile {profile} "
        f"--artifact-target {artifact_target} --json"
    )
    items = [
        PrereleaseChecklistItem(
            name="lint",
            command="uv run ruff check .",
            evidence="ruff exits 0",
        ),
        PrereleaseChecklistItem(
            name="tests",
            command="uv run pytest -q",
            evidence="pytest exits 0",
        ),
        PrereleaseChecklistItem(
            name="diff-check",
            command="git diff --check",
            evidence="no whitespace errors",
        ),
        PrereleaseChecklistItem(
            name="app-conformance",
            command=f"core check-app --all{app_args} --json",
            evidence="all app conformance results ok",
        ),
        PrereleaseChecklistItem(
            name="permission-catalog",
            command=f"core permissions catalog{app_args} --json",
            evidence="permission catalog exits 0",
        ),
        PrereleaseChecklistItem(
            name="migration-plan",
            command=f"core migrate plan{app_args} --json",
            evidence="migration plan exits 0",
        ),
        PrereleaseChecklistItem(
            name="release-checkpoint",
            command=release_command,
            evidence="release checkpoint ok",
        ),
    ]
    if profile in {"private", "cloud"}:
        items.append(
            PrereleaseChecklistItem(
                name="dependency-probes",
                command=release_command.replace(" --json", " --probe-dependencies --json"),
                evidence="external dependency probes pass",
            )
        )
    return PrereleaseChecklist(
        profile=profile,
        artifact_target=artifact_target,
        installed_apps=tuple(installed_apps),
        items=tuple(items),
    )


def _installed_app_args(installed_apps: list[str] | tuple[str, ...]) -> str:
    return "".join(f" --installed-app {module_path}" for module_path in installed_apps)
