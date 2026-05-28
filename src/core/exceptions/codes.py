import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ErrorCodeSpec:
    code: str
    default_http_status: int
    default_message: str
    owner_module: str = "core"
    details_schema: dict[str, Any] | None = None
    deprecated: bool = False
    headers: dict[str, str] = field(default_factory=dict)


_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


_ERROR_CODES: dict[str, ErrorCodeSpec] = {
    "VALIDATION_ERROR": ErrorCodeSpec("VALIDATION_ERROR", 400, "参数校验失败"),
    "AUTH_INVALID_TOKEN": ErrorCodeSpec("AUTH_INVALID_TOKEN", 401, "认证失败"),
    "USER_DISABLED": ErrorCodeSpec("USER_DISABLED", 403, "用户已禁用"),
    "TENANT_ACCESS_DENIED": ErrorCodeSpec("TENANT_ACCESS_DENIED", 403, "无权访问该租户"),
    "TENANT_CONTEXT_CONFLICT": ErrorCodeSpec("TENANT_CONTEXT_CONFLICT", 403, "租户上下文冲突"),
    "TENANT_STATE_FORBIDDEN": ErrorCodeSpec(
        "TENANT_STATE_FORBIDDEN",
        403,
        "当前租户状态不允许该操作",
    ),
    "UPLOAD_REJECTED": ErrorCodeSpec("UPLOAD_REJECTED", 400, "文件上传被拒绝"),
    "HOST_NOT_ALLOWED": ErrorCodeSpec("HOST_NOT_ALLOWED", 400, "请求 Host 不被允许"),
    "REQUEST_TOO_LARGE": ErrorCodeSpec("REQUEST_TOO_LARGE", 413, "请求体过大"),
    "PERMISSION_DENIED": ErrorCodeSpec("PERMISSION_DENIED", 403, "无权限访问该资源"),
    "NOT_FOUND": ErrorCodeSpec("NOT_FOUND", 404, "资源不存在"),
    "CONFLICT": ErrorCodeSpec("CONFLICT", 409, "资源冲突"),
    "EXTERNAL_SERVICE_ERROR": ErrorCodeSpec("EXTERNAL_SERVICE_ERROR", 502, "外部服务错误"),
    "IDEMPOTENCY_KEY_CONFLICT": ErrorCodeSpec("IDEMPOTENCY_KEY_CONFLICT", 409, "幂等键冲突"),
    "IDEMPOTENCY_IN_PROGRESS": ErrorCodeSpec("IDEMPOTENCY_IN_PROGRESS", 409, "请求正在处理中"),
    "TASK_IDEMPOTENCY_KEY_CONFLICT": ErrorCodeSpec(
        "TASK_IDEMPOTENCY_KEY_CONFLICT",
        409,
        "任务幂等键冲突",
        owner_module="core.tasks",
    ),
    "LOCK_NOT_ACQUIRED": ErrorCodeSpec("LOCK_NOT_ACQUIRED", 409, "资源正在处理中"),
    "QUOTA_EXCEEDED": ErrorCodeSpec("QUOTA_EXCEEDED", 403, "配额不足"),
    "RATE_LIMITED": ErrorCodeSpec("RATE_LIMITED", 429, "请求过于频繁"),
    "SYSTEM_ERROR": ErrorCodeSpec("SYSTEM_ERROR", 500, "系统错误"),
}


def _validate_error_code_spec(spec: ErrorCodeSpec) -> None:
    if not _CODE_PATTERN.fullmatch(spec.code):
        raise ValueError(f"Invalid error code: {spec.code!r}")
    if spec.default_http_status < 100 or spec.default_http_status > 599:
        raise ValueError(f"Invalid HTTP status for error code {spec.code!r}")
    if not spec.default_message:
        raise ValueError(f"Default message is required for error code {spec.code!r}")
    if not spec.owner_module:
        raise ValueError(f"Owner module is required for error code {spec.code!r}")


def register_error_codes(*specs: ErrorCodeSpec, replace: bool = False) -> None:
    seen: set[str] = set()
    for spec in specs:
        _validate_error_code_spec(spec)
        if spec.code in seen:
            raise ValueError(f"Duplicate error code registration: {spec.code}")
        seen.add(spec.code)
        existing = _ERROR_CODES.get(spec.code)
        if existing is not None and existing != spec and not replace:
            raise ValueError(f"Error code already registered: {spec.code}")

    for spec in specs:
        _ERROR_CODES[spec.code] = spec


def get_error_code(code: str) -> ErrorCodeSpec:
    return _ERROR_CODES.get(code) or ErrorCodeSpec(code, 500, "系统错误")


def is_error_code_registered(code: str) -> bool:
    return code in _ERROR_CODES


def require_error_code(code: str) -> ErrorCodeSpec:
    spec = _ERROR_CODES.get(code)
    if spec is None:
        raise ValueError(f"Unregistered error code: {code}")
    return spec


def iter_error_codes() -> tuple[ErrorCodeSpec, ...]:
    return tuple(_ERROR_CODES.values())
