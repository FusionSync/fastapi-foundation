from __future__ import annotations

from core.exceptions import AppError


def cache_key(namespace: str, *parts: str) -> str:
    segments = (namespace, *parts)
    invalid = [segment for segment in segments if not segment.strip() or ":" in segment]
    if invalid:
        raise AppError(
            "VALIDATION_ERROR",
            "Cache key segments must be non-empty and must not contain ':'",
            status_code=400,
        )
    return ":".join(segments)


def tenant_settings_cache_key(tenant_id: str) -> str:
    return cache_key("tenant", f"tenant_id={tenant_id}", "settings")


def tenant_lifecycle_cache_key(tenant_id: str) -> str:
    return cache_key("tenant", f"tenant_id={tenant_id}", "lifecycle")


def permission_cache_key(tenant_id: str) -> str:
    return cache_key("permission", f"tenant_id={tenant_id}")


def permission_subject_cache_key(
    tenant_id: str,
    subject_type: str,
    subject_id: str,
) -> str:
    return cache_key(
        "permission",
        f"tenant_id={tenant_id}",
        f"subject_type={subject_type}",
        f"subject_id={subject_id}",
    )


def permission_role_grant_cache_key(tenant_id: str, grant_id: str) -> str:
    return cache_key("permission", f"tenant_id={tenant_id}", f"grant_id={grant_id}")
