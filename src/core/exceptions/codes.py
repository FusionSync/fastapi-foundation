import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ErrorCodeSpec:
    code: str
    default_http_status: int
    default_message: str
    owner_module: str | None = None
    details_schema: dict[str, Any] | None = None
    deprecated: bool | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModuleErrorCode:
    code: str
    default_http_status: int
    default_message: str
    details_schema: dict[str, Any] | None = None
    deprecated: bool = False
    headers: dict[str, str] = field(default_factory=dict)


_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _core_error_code(
    code: str,
    default_http_status: int,
    default_message: str,
    *,
    owner_module: str = "core.exceptions",
    details_schema: dict[str, Any] | None = None,
    deprecated: bool = False,
    headers: dict[str, str] | None = None,
) -> ErrorCodeSpec:
    return ErrorCodeSpec(
        code,
        default_http_status,
        default_message,
        owner_module=owner_module,
        details_schema=details_schema or {},
        deprecated=deprecated,
        headers=headers or {},
    )


_ERROR_CODES: dict[str, ErrorCodeSpec] = {
    "VALIDATION_ERROR": _core_error_code("VALIDATION_ERROR", 400, "参数校验失败"),
    "AUTH_INVALID_TOKEN": _core_error_code("AUTH_INVALID_TOKEN", 401, "认证失败"),
    "USER_DISABLED": _core_error_code("USER_DISABLED", 403, "用户已禁用"),
    "TENANT_ACCESS_DENIED": _core_error_code("TENANT_ACCESS_DENIED", 403, "无权访问该租户"),
    "TENANT_CONTEXT_CONFLICT": _core_error_code(
        "TENANT_CONTEXT_CONFLICT",
        403,
        "租户上下文冲突",
    ),
    "TENANT_STATE_FORBIDDEN": _core_error_code(
        "TENANT_STATE_FORBIDDEN",
        403,
        "当前租户状态不允许该操作",
    ),
    "TENANT_DELETE_STEP_FAILED": _core_error_code(
        "TENANT_DELETE_STEP_FAILED",
        409,
        "租户删除步骤失败",
        owner_module="core.tenancy",
        details_schema={
            "tenant_id": "str",
            "step": "str",
            "attempt_count": "int",
            "forward_fix_required": "bool",
        },
    ),
    "UPLOAD_REJECTED": _core_error_code("UPLOAD_REJECTED", 400, "文件上传被拒绝"),
    "HOST_NOT_ALLOWED": _core_error_code("HOST_NOT_ALLOWED", 400, "请求 Host 不被允许"),
    "REQUEST_TOO_LARGE": _core_error_code("REQUEST_TOO_LARGE", 413, "请求体过大"),
    "PERMISSION_DENIED": _core_error_code("PERMISSION_DENIED", 403, "无权限访问该资源"),
    "NOT_FOUND": _core_error_code("NOT_FOUND", 404, "资源不存在"),
    "CONFLICT": _core_error_code("CONFLICT", 409, "资源冲突"),
    "EXTERNAL_SERVICE_ERROR": _core_error_code(
        "EXTERNAL_SERVICE_ERROR",
        502,
        "外部服务错误",
    ),
    "IDEMPOTENCY_KEY_CONFLICT": _core_error_code(
        "IDEMPOTENCY_KEY_CONFLICT",
        409,
        "幂等键冲突",
    ),
    "IDEMPOTENCY_IN_PROGRESS": _core_error_code(
        "IDEMPOTENCY_IN_PROGRESS",
        409,
        "请求正在处理中",
    ),
    "TASK_IDEMPOTENCY_KEY_CONFLICT": _core_error_code(
        "TASK_IDEMPOTENCY_KEY_CONFLICT",
        409,
        "任务幂等键冲突",
        owner_module="core.tasks",
    ),
    "LOCK_NOT_ACQUIRED": _core_error_code("LOCK_NOT_ACQUIRED", 409, "资源正在处理中"),
    "QUOTA_EXCEEDED": _core_error_code("QUOTA_EXCEEDED", 403, "配额不足"),
    "RATE_LIMITED": _core_error_code("RATE_LIMITED", 429, "请求过于频繁"),
    "SYSTEM_ERROR": _core_error_code("SYSTEM_ERROR", 500, "系统错误"),
}


def validate_error_code_spec(spec: ErrorCodeSpec) -> None:
    if not _CODE_PATTERN.fullmatch(spec.code):
        raise ValueError(f"Invalid error code: {spec.code!r}")
    if spec.default_http_status < 100 or spec.default_http_status > 599:
        raise ValueError(f"Invalid HTTP status for error code {spec.code!r}")
    if not spec.default_message:
        raise ValueError(f"Default message is required for error code {spec.code!r}")
    if not spec.owner_module:
        raise ValueError(f"Owner module metadata is required for error code {spec.code!r}")
    if spec.details_schema is None:
        raise ValueError(f"Details schema metadata is required for error code {spec.code!r}")
    if not isinstance(spec.details_schema, dict):
        raise ValueError(f"Details schema metadata must be a dict for error code {spec.code!r}")
    if spec.deprecated is None:
        raise ValueError(f"Deprecated flag metadata is required for error code {spec.code!r}")
    if not isinstance(spec.deprecated, bool):
        raise ValueError(f"Deprecated flag metadata must be a bool for error code {spec.code!r}")
    if not isinstance(spec.headers, dict):
        raise ValueError(f"Headers metadata must be a dict for error code {spec.code!r}")


def define_module_error_codes(
    owner_module: str,
    *codes: ModuleErrorCode,
    code_prefix: str | None = None,
) -> list[ErrorCodeSpec]:
    owner = owner_module.strip()
    if not owner:
        raise ValueError("owner_module is required")
    prefix = code_prefix if code_prefix is not None else f"{owner.upper()}_"
    if not _CODE_PATTERN.fullmatch(f"{prefix}X"):
        raise ValueError(f"Invalid module error code prefix: {prefix!r}")

    specs: list[ErrorCodeSpec] = []
    seen: set[str] = set()
    for code in codes:
        if not isinstance(code, ModuleErrorCode):
            raise TypeError("module error code must be ModuleErrorCode")
        if not code.code.startswith(prefix):
            raise ValueError(
                f"Module error code {code.code!r} must start with module prefix {prefix!r}"
            )
        spec = ErrorCodeSpec(
            code.code,
            code.default_http_status,
            code.default_message,
            owner_module=owner,
            details_schema=code.details_schema or {},
            deprecated=code.deprecated,
            headers=dict(code.headers),
        )
        validate_error_code_spec(spec)
        if spec.code in seen:
            raise ValueError(f"Duplicate module error code: {spec.code}")
        seen.add(spec.code)
        specs.append(spec)
    return specs


def register_error_codes(*specs: ErrorCodeSpec, replace: bool = False) -> None:
    seen: set[str] = set()
    for spec in specs:
        validate_error_code_spec(spec)
        if spec.code in seen:
            raise ValueError(f"Duplicate error code registration: {spec.code}")
        seen.add(spec.code)
        existing = _ERROR_CODES.get(spec.code)
        if existing is not None and existing != spec and not replace:
            raise ValueError(f"Error code already registered: {spec.code}")

    for spec in specs:
        _ERROR_CODES[spec.code] = spec


def get_error_code(code: str) -> ErrorCodeSpec:
    return _ERROR_CODES.get(code) or _core_error_code(code, 500, "系统错误")


def is_error_code_registered(code: str) -> bool:
    return code in _ERROR_CODES


def require_error_code(code: str) -> ErrorCodeSpec:
    spec = _ERROR_CODES.get(code)
    if spec is None:
        raise ValueError(f"Unregistered error code: {code}")
    return spec


def iter_error_codes() -> tuple[ErrorCodeSpec, ...]:
    return tuple(_ERROR_CODES.values())
