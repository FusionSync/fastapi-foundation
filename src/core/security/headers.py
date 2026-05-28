from __future__ import annotations

from dataclasses import dataclass

from core.exceptions import AppError


@dataclass(frozen=True, slots=True)
class SecurityHeadersConfig:
    content_security_policy: str | None = (
        "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
    )
    hsts_max_age_seconds: int | None = None
    hsts_include_subdomains: bool = True
    hsts_preload: bool = False
    frame_options: str = "DENY"
    referrer_policy: str = "no-referrer"
    permissions_policy: str = "camera=(), geolocation=(), microphone=()"

    def __post_init__(self) -> None:
        if self.hsts_max_age_seconds is not None and self.hsts_max_age_seconds <= 0:
            raise AppError(
                "VALIDATION_ERROR",
                "HSTS max age must be greater than zero",
                status_code=400,
            )


def security_headers(config: SecurityHeadersConfig | None = None) -> dict[str, str]:
    resolved = config or SecurityHeadersConfig()
    headers = {
        "Cross-Origin-Opener-Policy": "same-origin",
        "Permissions-Policy": resolved.permissions_policy,
        "Referrer-Policy": resolved.referrer_policy,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": resolved.frame_options,
        "X-Permitted-Cross-Domain-Policies": "none",
    }
    if resolved.content_security_policy:
        headers["Content-Security-Policy"] = resolved.content_security_policy
    if resolved.hsts_max_age_seconds is not None:
        value = f"max-age={resolved.hsts_max_age_seconds}"
        if resolved.hsts_include_subdomains:
            value += "; includeSubDomains"
        if resolved.hsts_preload:
            value += "; preload"
        headers["Strict-Transport-Security"] = value
    return dict(sorted(headers.items()))
