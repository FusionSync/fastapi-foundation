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
