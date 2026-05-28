from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from core.exceptions import AppError

DEFAULT_ALLOWED_UPLOAD_CONTENT_TYPES: dict[str, tuple[str, ...]] = {
    ".csv": ("text/csv", "application/csv", "application/vnd.ms-excel"),
    ".doc": ("application/msword",),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".jpeg": ("image/jpeg",),
    ".jpg": ("image/jpeg",),
    ".pdf": ("application/pdf",),
    ".png": ("image/png",),
    ".txt": ("text/plain",),
    ".xls": ("application/vnd.ms-excel",),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".zip": ("application/zip", "application/x-zip-compressed"),
}


@dataclass(frozen=True, slots=True)
class UploadValidationResult:
    file_name: str
    content_type: str
    extension: str
    size: int
    checksum: str


@dataclass(frozen=True, slots=True)
class UploadSecurityPolicy:
    max_bytes: int = 50 * 1024 * 1024
    allowed_content_types: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_ALLOWED_UPLOAD_CONTENT_TYPES)
    )

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "Upload max_bytes must be greater than zero",
                status_code=400,
            )
        if not self.allowed_content_types:
            raise AppError(
                "VALIDATION_ERROR",
                "Upload allowed_content_types must not be empty",
                status_code=400,
            )
        for extension, content_types in self.allowed_content_types.items():
            if not extension.startswith(".") or not extension.strip():
                raise AppError(
                    "VALIDATION_ERROR",
                    "Upload extension must start with '.'",
                    status_code=400,
                )
            if not content_types:
                raise AppError(
                    "VALIDATION_ERROR",
                    f"Upload extension {extension!r} must declare content types",
                    status_code=400,
                )


DEFAULT_UPLOAD_SECURITY_POLICY = UploadSecurityPolicy()


def validate_upload(
    *,
    file_name: str,
    content_type: str,
    data: bytes,
    policy: UploadSecurityPolicy | None = None,
    expected_checksum: str | None = None,
) -> UploadValidationResult:
    resolved_policy = policy or DEFAULT_UPLOAD_SECURITY_POLICY
    normalized_name = _validate_file_name(file_name=file_name, content_type=content_type, data=data)
    normalized_content_type = content_type.strip().lower()
    extension = _extension(normalized_name)
    size = len(data)
    if size > resolved_policy.max_bytes:
        _reject(
            "file_too_large",
            file_name=normalized_name,
            content_type=normalized_content_type,
            size=size,
            max_bytes=resolved_policy.max_bytes,
        )
    allowed_types = {
        item.lower()
        for item in resolved_policy.allowed_content_types.get(extension, ())
    }
    if not allowed_types:
        _reject(
            "extension_not_allowed",
            file_name=normalized_name,
            content_type=normalized_content_type,
            size=size,
            extension=extension,
        )
    if normalized_content_type not in allowed_types:
        _reject(
            "content_type_not_allowed",
            file_name=normalized_name,
            content_type=normalized_content_type,
            size=size,
            extension=extension,
            allowed_content_types=sorted(allowed_types),
        )
    checksum = hashlib.sha256(data).hexdigest()
    if expected_checksum is not None and checksum != expected_checksum.lower():
        _reject(
            "checksum_mismatch",
            file_name=normalized_name,
            content_type=normalized_content_type,
            size=size,
            expected_checksum=expected_checksum,
            checksum=checksum,
        )
    return UploadValidationResult(
        file_name=normalized_name,
        content_type=normalized_content_type,
        extension=extension,
        size=size,
        checksum=checksum,
    )


def _validate_file_name(*, file_name: str, content_type: str, data: bytes) -> str:
    normalized_name = file_name.strip()
    if (
        not normalized_name
        or "/" in normalized_name
        or "\\" in normalized_name
        or "\x00" in normalized_name
        or normalized_name in {".", ".."}
    ):
        _reject(
            "invalid_file_name",
            file_name=file_name,
            content_type=content_type,
            size=len(data) if isinstance(data, bytes) else 0,
        )
    if not content_type.strip():
        _reject(
            "missing_content_type",
            file_name=normalized_name,
            content_type=content_type,
            size=0,
        )
    if not isinstance(data, bytes) or not data:
        _reject("empty_file", file_name=normalized_name, content_type=content_type, size=0)
    return normalized_name


def _extension(file_name: str) -> str:
    if "." not in file_name:
        return ""
    return f".{file_name.rsplit('.', maxsplit=1)[1].lower()}"


def _reject(reason: str, **details: object) -> None:
    raise AppError(
        "UPLOAD_REJECTED",
        "Upload rejected by security policy",
        status_code=400,
        details={"reason": reason, **details},
    )
