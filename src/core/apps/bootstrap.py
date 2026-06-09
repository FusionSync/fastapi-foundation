from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_PACKAGE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class AppBootstrapError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AppBootstrapResult:
    label: str
    module_path: str
    target_dir: Path
    target_root: Path
    _relative_files: tuple[str, ...]

    @property
    def relative_files(self) -> list[str]:
        return list(self._relative_files)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "label": self.label,
            "module_path": self.module_path,
            "target_dir": str(self.target_dir),
            "files": self.relative_files,
        }


def bootstrap_app(
    label: str,
    *,
    target_root: str | Path = "src",
    package: str = "apps",
) -> AppBootstrapResult:
    _validate_label(label)
    _validate_package(package)

    resolved_target_root = Path(target_root)
    package_dir = resolved_target_root.joinpath(*package.split("."))
    target_dir = package_dir / label
    if target_dir.exists():
        raise AppBootstrapError(f"target app directory already exists: {target_dir}")

    context = _TemplateContext(label=label, package=package)
    rendered_files = _render_app_files(context)
    created_files: list[Path] = []

    package_dir.mkdir(parents=True, exist_ok=True)
    package_init = package_dir / "__init__.py"
    if not package_init.exists():
        package_init.write_text("", encoding="utf-8")
        created_files.append(package_init)

    target_dir.mkdir(parents=True, exist_ok=False)
    for relative_path, content in rendered_files.items():
        file_path = target_dir / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        created_files.append(file_path)

    relative_files = tuple(
        sorted(path.relative_to(resolved_target_root).as_posix() for path in created_files)
    )
    return AppBootstrapResult(
        label=label,
        module_path=f"{package}.{label}.module",
        target_dir=target_dir,
        target_root=resolved_target_root,
        _relative_files=relative_files,
    )


@dataclass(frozen=True, slots=True)
class _TemplateContext:
    label: str
    package: str

    @property
    def pascal_name(self) -> str:
        return "".join(part.capitalize() for part in self.label.split("_"))

    @property
    def route_prefix(self) -> str:
        return self.label.replace("_", "-")

    @property
    def title(self) -> str:
        return self.label.replace("_", " ").title()

    @property
    def label_upper(self) -> str:
        return self.label.upper()

    @property
    def module_package(self) -> str:
        return f"{self.package}.{self.label}"


def _validate_label(label: str) -> None:
    if not _LABEL_PATTERN.fullmatch(label):
        raise AppBootstrapError(
            "app label must match ^[a-z][a-z0-9_]*$ and use snake_case"
        )


def _validate_package(package: str) -> None:
    if not _PACKAGE_PATTERN.fullmatch(package):
        raise AppBootstrapError(
            "package must be a dotted snake_case Python package path"
        )


def _render_app_files(context: _TemplateContext) -> dict[str, str]:
    replacements = {
        "__LABEL__": context.label,
        "__PASCAL__": context.pascal_name,
        "__ROUTE_PREFIX__": context.route_prefix,
        "__TITLE__": context.title,
        "__LABEL_UPPER__": context.label_upper,
        "__MODULE_PACKAGE__": context.module_package,
    }
    return {
        _replace_tokens(path, replacements): _replace_tokens(template, replacements)
        for path, template in _TEMPLATES.items()
    }


def _replace_tokens(template: str, replacements: dict[str, str]) -> str:
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered


_TEMPLATES: dict[str, str] = {
    "__init__.py": '''"""__TITLE__ app package."""\n''',
    "models.py": """from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from core.base.models import IdMixin, TenantScopedModel, TimestampMixin


class __PASCAL__Record(IdMixin, TimestampMixin, TenantScopedModel):
    __tablename__ = "__LABEL___records"

    title: Mapped[str] = mapped_column(String(128), nullable=False)
""",
    "schemas.py": """from core.base import Schema, CreateSchema, ReadSchema, UpdateSchema


class __PASCAL__Create(CreateSchema):
    title: str


class __PASCAL__Update(UpdateSchema):
    title: str | None = None


class __PASCAL__Read(ReadSchema):
    tenant_id: str
    title: str


class __PASCAL__Status(Schema):
    app: str
    status: str
""",
    "services.py": """from __MODULE_PACKAGE__.schemas import __PASCAL__Status
from core.base.services import BaseService


class __PASCAL__Service(BaseService):
    def status(self) -> __PASCAL__Status:
        return __PASCAL__Status(app="__LABEL__", status="ready")
""",
    "router.py": """from __MODULE_PACKAGE__.schemas import __PASCAL__Status
from __MODULE_PACKAGE__.services import __PASCAL__Service
from core.base import create_router
from core.serialization import Envelope, ok

router = create_router(
    "/__ROUTE_PREFIX__",
    tags=["__LABEL__"],
    permissions=["__LABEL__:read"],
    permission_scope="tenant",
)


@router.get("/status", response_model=Envelope[__PASCAL__Status])
async def get_status() -> dict[str, object]:
    return ok(__PASCAL__Service().status().model_dump())
""",
    "permissions.py": """from core.permissions import PermissionSpec

PERMISSIONS = [
    PermissionSpec(
        resource="__LABEL__",
        action="read",
        scope="tenant",
        description="Read __LABEL__ records",
    ),
    PermissionSpec(
        resource="__LABEL__",
        action="write",
        scope="tenant",
        description="Write __LABEL__ records",
    ),
]
""",
    "errors.py": """from core.exceptions import ModuleErrorCode, define_module_error_codes

__LABEL_UPPER___NOT_READY = "__LABEL_UPPER___NOT_READY"

ERROR_CODES = define_module_error_codes(
    "__LABEL__",
    ModuleErrorCode(
        __LABEL_UPPER___NOT_READY,
        409,
        "__TITLE__ is not ready",
        details_schema={"reason": "str"},
    ),
)

__all__ = ["ERROR_CODES", "__LABEL_UPPER___NOT_READY"]
""",
    "error_messages.py": """from __MODULE_PACKAGE__.errors import (
    ERROR_CODES,
    __LABEL_UPPER___NOT_READY,
)
from core.messages import ModuleMessageCatalog, define_module_message_catalogs

MESSAGE_CATALOGS = define_module_message_catalogs(
    "__LABEL__",
    error_codes=ERROR_CODES,
    catalogs=[
        ModuleMessageCatalog(
            locale="en-US",
            messages={__LABEL_UPPER___NOT_READY: "__TITLE__ is not ready"},
        )
    ],
)

__all__ = ["MESSAGE_CATALOGS"]
""",
    "module.py": """from __MODULE_PACKAGE__.errors import ERROR_CODES
from __MODULE_PACKAGE__.error_messages import MESSAGE_CATALOGS
from __MODULE_PACKAGE__.permissions import PERMISSIONS
from __MODULE_PACKAGE__.router import router
from core.apps import AppModule, MigrationSpec

module = AppModule(
    label="__LABEL__",
    version="0.1.0",
    dependencies=[],
    routers=[router],
    models=["__MODULE_PACKAGE__.models"],
    migrations=MigrationSpec(path="__MODULE_PACKAGE__.migrations"),
    permissions=PERMISSIONS,
    error_codes=ERROR_CODES,
    message_catalogs=MESSAGE_CATALOGS,
    public_api=["__MODULE_PACKAGE__.public_api"],
)
""",
    "public_api.py": """from __MODULE_PACKAGE__.schemas import __PASCAL__Status
from __MODULE_PACKAGE__.services import __PASCAL__Service

__all__ = ["__PASCAL__Service", "__PASCAL__Status"]
""",
    "events.py": """__all__: list[str] = []
""",
    "tasks.py": """__all__: list[str] = []
""",
    "migrations/__init__.py": "",
    "migrations/manifest.py": """from core.migrations import MigrationManifest

MIGRATIONS: list[MigrationManifest] = []
""",
    "tests/test___LABEL___contract.py": """from core.apps.conformance import check_app


def test___LABEL___app_conformance() -> None:
    result = check_app("__MODULE_PACKAGE__.module")

    assert result.ok is True
    assert result.errors == []
""",
}
