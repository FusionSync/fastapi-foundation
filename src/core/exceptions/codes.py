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


_ERROR_CODES: dict[str, ErrorCodeSpec] = {
    "VALIDATION_ERROR": ErrorCodeSpec("VALIDATION_ERROR", 400, "参数校验失败"),
    "AUTH_INVALID_TOKEN": ErrorCodeSpec("AUTH_INVALID_TOKEN", 401, "认证失败"),
    "TENANT_ACCESS_DENIED": ErrorCodeSpec("TENANT_ACCESS_DENIED", 403, "无权访问该租户"),
    "TENANT_CONTEXT_CONFLICT": ErrorCodeSpec("TENANT_CONTEXT_CONFLICT", 403, "租户上下文冲突"),
    "TENANT_STATE_FORBIDDEN": ErrorCodeSpec(
        "TENANT_STATE_FORBIDDEN",
        403,
        "当前租户状态不允许该操作",
    ),
    "UPLOAD_REJECTED": ErrorCodeSpec("UPLOAD_REJECTED", 400, "文件上传被拒绝"),
    "PERMISSION_DENIED": ErrorCodeSpec("PERMISSION_DENIED", 403, "无权限访问该资源"),
    "NOT_FOUND": ErrorCodeSpec("NOT_FOUND", 404, "资源不存在"),
    "CONFLICT": ErrorCodeSpec("CONFLICT", 409, "资源冲突"),
    "EXTERNAL_SERVICE_ERROR": ErrorCodeSpec("EXTERNAL_SERVICE_ERROR", 502, "外部服务错误"),
    "IDEMPOTENCY_KEY_CONFLICT": ErrorCodeSpec("IDEMPOTENCY_KEY_CONFLICT", 409, "幂等键冲突"),
    "IDEMPOTENCY_IN_PROGRESS": ErrorCodeSpec("IDEMPOTENCY_IN_PROGRESS", 409, "请求正在处理中"),
    "LOCK_NOT_ACQUIRED": ErrorCodeSpec("LOCK_NOT_ACQUIRED", 409, "资源正在处理中"),
    "QUOTA_EXCEEDED": ErrorCodeSpec("QUOTA_EXCEEDED", 403, "配额不足"),
    "RATE_LIMITED": ErrorCodeSpec("RATE_LIMITED", 429, "请求过于频繁"),
    "SYSTEM_ERROR": ErrorCodeSpec("SYSTEM_ERROR", 500, "系统错误"),
}


def get_error_code(code: str) -> ErrorCodeSpec:
    return _ERROR_CODES.get(code) or ErrorCodeSpec(code, 500, "系统错误")
