import hashlib

import pytest

from core.exceptions import AppError
from core.security import (
    SecurityHeadersConfig,
    UploadSecurityPolicy,
    security_headers,
    validate_upload,
)


def test_validate_upload_accepts_allowed_file_and_returns_metadata() -> None:
    data = b"docx-bytes"
    result = validate_upload(
        file_name="proposal.DOCX",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        data=data,
        policy=UploadSecurityPolicy(max_bytes=1024),
    )

    assert result.file_name == "proposal.DOCX"
    assert result.extension == ".docx"
    assert result.content_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert result.size == len(data)
    assert result.checksum == hashlib.sha256(data).hexdigest()


def test_validate_upload_rejects_oversized_file_with_stable_code() -> None:
    with pytest.raises(AppError) as rejected:
        validate_upload(
            file_name="proposal.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=b"12345",
            policy=UploadSecurityPolicy(max_bytes=4),
        )

    assert rejected.value.code == "UPLOAD_REJECTED"
    assert rejected.value.status_code == 400
    assert rejected.value.details == {
        "reason": "file_too_large",
        "file_name": "proposal.docx",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size": 5,
        "max_bytes": 4,
    }


def test_validate_upload_rejects_unsafe_name_extension_and_mime() -> None:
    policy = UploadSecurityPolicy(
        max_bytes=1024,
        allowed_content_types={".pdf": ("application/pdf",)},
    )

    with pytest.raises(AppError) as unsafe_name:
        validate_upload(
            file_name="../secret.pdf",
            content_type="application/pdf",
            data=b"pdf",
            policy=policy,
        )
    with pytest.raises(AppError) as extension:
        validate_upload(
            file_name="script.exe",
            content_type="application/octet-stream",
            data=b"exe",
            policy=policy,
        )
    with pytest.raises(AppError) as mime:
        validate_upload(
            file_name="proposal.pdf",
            content_type="application/octet-stream",
            data=b"pdf",
            policy=policy,
        )

    assert unsafe_name.value.details is not None
    assert unsafe_name.value.details["reason"] == "invalid_file_name"
    assert extension.value.details is not None
    assert extension.value.details["reason"] == "extension_not_allowed"
    assert mime.value.details is not None
    assert mime.value.details["reason"] == "content_type_not_allowed"


def test_validate_upload_rejects_checksum_mismatch() -> None:
    with pytest.raises(AppError) as mismatch:
        validate_upload(
            file_name="proposal.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=b"docx-bytes",
            expected_checksum="0" * 64,
            policy=UploadSecurityPolicy(max_bytes=1024),
        )

    assert mismatch.value.code == "UPLOAD_REJECTED"
    assert mismatch.value.details is not None
    assert mismatch.value.details["reason"] == "checksum_mismatch"
    assert mismatch.value.details["expected_checksum"] == "0" * 64


def test_security_headers_include_defensive_defaults_and_optional_hsts() -> None:
    headers = security_headers(
        SecurityHeadersConfig(
            hsts_max_age_seconds=31536000,
            content_security_policy="default-src 'self'",
        )
    )

    assert headers == {
        "Content-Security-Policy": "default-src 'self'",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
        "Referrer-Policy": "no-referrer",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-Permitted-Cross-Domain-Policies": "none",
    }


def test_security_headers_config_validates_hsts() -> None:
    with pytest.raises(AppError) as invalid_hsts:
        SecurityHeadersConfig(hsts_max_age_seconds=0)

    assert invalid_hsts.value.code == "VALIDATION_ERROR"
