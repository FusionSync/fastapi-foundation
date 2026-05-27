# Core Exceptions

## 职责

Exceptions 模块定义领域异常、系统异常和异常到统一响应 envelope 的映射。

## 目录建议

```text
src/core/exceptions/
  base.py
  handlers.py
  codes.py
```

## 异常类型

```text
AppError
  code
  message
  details

ValidationAppError
PermissionAppError
NotFoundAppError
ConflictAppError
ExternalServiceAppError
SystemAppError
```

## 处理策略

- 业务已知异常转换为对应业务 code。
- Pydantic/FastAPI validation error 转换为 `VALIDATION_ERROR`。
- 未知异常转换为 `SYSTEM_ERROR`，生产环境不暴露堆栈。
- 所有 JSON API 错误响应 HTTP status 为 200。

## 设计要求

- service 只抛 AppError 或其子类。
- router 不直接拼错误响应。
- exception handler 必须记录 request_id、user_id、tenant_id。
- details 必须先经过脱敏。
