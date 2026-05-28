from __future__ import annotations

from typing import Any

from core.exceptions import AppError


class ExternalServiceAppError(AppError):
    def __init__(
        self,
        *,
        service_name: str,
        message: str,
        details: dict[str, Any],
    ) -> None:
        super().__init__(
            "EXTERNAL_SERVICE_ERROR",
            f"{message}: {service_name}",
            status_code=502,
            details=details,
        )
        self.service_name = service_name
