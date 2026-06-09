# Core Exceptions

## Progress

- Status: `connected`
- Done: app exception、错误码 catalog、模块错误码标准定义 helper、owner/deprecation/details schema metadata gate、业务 app 错误码 conformance 注册、tenant deletion step failure code、统一 handler 和 response envelope 已接入。
- Next: _none_

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
  status_code
  details
  headers

ValidationAppError
PermissionAppError
NotFoundAppError
ConflictAppError
ExternalServiceAppError
SystemAppError
```

## 处理策略

- 业务已知异常转换为对应业务 code。
- `AppError` 只能使用已登记到 error code registry 的 code；业务 app 扩展错误码时必须在 `AppModule.error_codes` 声明 `ErrorCodeSpec`，启动期 conformance 通过后由 app runtime 统一注册。
- Pydantic/FastAPI validation error 转换为 `VALIDATION_ERROR`。
- 未知异常转换为 `SYSTEM_ERROR`，生产环境不暴露堆栈。
- 默认使用标准 HTTP status，响应体 `code` 表达稳定业务语义。
- 只有显式启用 `API__ERROR_HTTP_STATUS_MODE=always_200` 时，业务错误才降级为 HTTP 200 兼容响应。
- exception handler 必须统一处理 `code -> status_code -> headers -> default_message` 映射。

## 当前实现

- `ErrorCodeSpec` 必须显式声明 `owner_module`、`details_schema` 和 `deprecated`；无 details 的错误码使用空 schema `{}` 表达。
- 业务模块默认使用 `ModuleErrorCode` + `define_module_error_codes()` 生成 `ErrorCodeSpec`，避免在 `module.py` 里重复手写 `owner_module`。
- `define_module_error_codes()` 默认要求 code 以模块 label 的大写前缀开头，例如 `orders -> ORDERS_`，作为字符串错误码的模块基码。
- `register_error_codes()` 会拒绝缺失 metadata、非法 HTTP status、重复 code 和非 dict 的 details schema。
- `check_app()` 会检查 app 声明的错误码 metadata，并要求 `owner_module` 等于 `AppModule.label`。
- `check_apps()` 和 `AppRegistry.load()` 会拒绝多个 app 声明同一个业务错误码。
- `AppRegistry.load()` 会统一注册所有 `AppModule.error_codes`，server、worker、scheduler 和 outbox 等运行角色都可以直接抛这些稳定 code。

## 设计要求

- service 只抛 AppError 或其子类。
- 禁止在 service 或 router 中临时拼接未登记的错误码；未登记 code 会在 `AppError` 构造时直接失败。业务 app 的 code 常量放在 `errors.py`，通过 `AppModule.error_codes` 注册。
- router 不直接拼错误响应。
- exception handler 必须记录 request_id、user_id、tenant_id。
- details 必须先经过脱敏。
- `Retry-After`、`WWW-Authenticate`、`X-App-Code` 等响应头由 core exception/response 层统一生成。
