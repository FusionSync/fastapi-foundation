from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.exceptions.base import AppError
from core.exceptions.codes import get_error_code
from core.serialization.responses import fail


def _status_code(request: Request, default_status: int) -> int:
    settings = getattr(request.app.state, "settings", None)
    if settings and settings.api.error_http_status_mode == "always_200":
        return 200
    return default_status


def _headers(request: Request, code: str, headers: dict[str, str] | None = None) -> dict[str, str]:
    result = dict(headers or {})
    result["X-App-Code"] = code
    return result


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        spec = get_error_code(exc.code)
        status = exc.status_code or spec.default_http_status
        return JSONResponse(
            fail(exc.code, message=exc.message or spec.default_message, details=exc.details),
            status_code=_status_code(request, status),
            headers=_headers(request, exc.code, {**spec.headers, **exc.headers}),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        spec = get_error_code("VALIDATION_ERROR")
        return JSONResponse(
            fail(
                spec.code,
                message=spec.default_message,
                details={"errors": exc.errors()},
            ),
            status_code=_status_code(request, spec.default_http_status),
            headers=_headers(request, spec.code),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        spec = get_error_code("SYSTEM_ERROR")
        return JSONResponse(
            fail(spec.code, message=spec.default_message),
            status_code=_status_code(request, spec.default_http_status),
            headers=_headers(request, spec.code),
        )
