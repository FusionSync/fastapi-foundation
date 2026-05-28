# Core API Conventions

## 职责

API Conventions 定义全项目统一的路由、响应、错误、分页、过滤和版本规范。

## 路由前缀

```text
/api/v1
```

## 响应格式

所有 JSON API 统一使用响应 envelope。HTTP status 表达协议层和通用 Web 语义，响应体 `code` 表达稳定业务语义。

单对象响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": {
    "id": "xxx"
  },
  "list": null,
  "pagination": null,
  "details": null,
  "request_id": "req_xxx"
}
```

列表响应：

```json
{
  "code": "OK",
  "message": "success",
  "data": null,
  "list": [],
  "pagination": {
    "total": 0,
    "page": 1,
    "page_size": 20,
    "has_next": false
  },
  "details": null,
  "request_id": "req_xxx"
}
```

业务失败响应：

```json
{
  "code": "PERMISSION_DENIED",
  "message": "无权限访问该资源",
  "data": null,
  "list": null,
  "pagination": null,
  "details": {
    "resource": "workspace",
    "action": "write"
  },
  "request_id": "req_xxx"
}
```

## HTTP 状态码策略

- JSON 成功响应使用 `200/201/202` 并返回 envelope。
- `204` 只允许用于明确无 body 的非 JSON 接口；业务 JSON API 不使用 `204`，避免丢失 `request_id`。
- 参数错误使用 `400 + VALIDATION_ERROR`。
- 上传安全校验失败使用 `400 + UPLOAD_REJECTED`。
- 未认证使用 `401 + AUTH_INVALID_TOKEN`，并保留 `WWW-Authenticate`。
- 无权限使用 `403 + PERMISSION_DENIED`。
- 配额不足使用 `403 + QUOTA_EXCEEDED`。
- 资源不存在使用 `404 + *_NOT_FOUND`。
- 资源冲突、幂等冲突或锁竞争使用 `409 + CONFLICT/IDEMPOTENCY_KEY_CONFLICT/LOCK_NOT_ACQUIRED`。
- 限流使用 `429 + RATE_LIMITED`，并返回 `Retry-After`。
- 外部依赖错误使用 `502 + EXTERNAL_SERVICE_ERROR`；后续如需区分超时/熔断，再增加稳定子码。
- 未知系统错误使用 `500 + SYSTEM_ERROR`。
- 二进制下载成功仍返回 HTTP 200 stream，不使用 JSON envelope。
- 下载失败时返回 JSON envelope，并使用对应错误 HTTP status。
- 反向代理、网络、进程崩溃、框架未捕获异常可能产生非 200，这属于应用外或兜底故障。
- 监控必须同时记录 HTTP status 和业务 `code`，不能只看 HTTP status。

## 兼容模式

如果特定客户端或旧系统强制要求“业务错误也 HTTP 200”，只能通过显式配置启用兼容模式：

```text
API__ERROR_HTTP_STATUS_MODE=always_200
```

兼容模式必须同时满足：

- 响应头包含 `X-App-Code` 和 `X-Request-ID`。
- `code != OK` 时默认加 `Cache-Control: no-store`。
- SDK 在 `code != OK` 时必须抛异常。
- API Gateway/Ingress 必须采集 `X-App-Code` 作为告警标签。
- 限流响应仍必须提供 `Retry-After`。

默认生产模式应使用标准 HTTP status。

## 分页规范

```text
page
page_size
```

默认 `page_size=20`，最大值由配置控制。

## 过滤和排序

简单过滤使用 query params：

```text
?status=active&keyword=demo
```

排序使用：

```text
?sort=-created_at,name
```

## 错误码

错误码按模块命名：

```text
OK
AUTH_INVALID_TOKEN
TENANT_NOT_FOUND
PERMISSION_DENIED
FILE_NOT_FOUND
VALIDATION_ERROR
SYSTEM_ERROR
```

错误码必须进入统一 registry。registry 至少包含：

```text
code
default_http_status
default_message
details_schema
owner_module
deprecated
```

同一个语义只能有一个稳定 code，禁止不同 app 重复定义语义相同的错误码。CI/contract test 必须校验 code 唯一性、HTTP status 映射和 OpenAPI envelope 一致性。

错误响应的 `message` 由 `core.messages` 解析。业务可以显式传 message，但默认应只抛稳定 code 和 details，让 core 根据 locale/catalog 生成最终文案。`message` 不作为稳定接口契约，客户端逻辑必须依赖 `code`。

## 设计要求

- router 只做入参、依赖和响应，不放复杂业务逻辑。
- service 抛出领域异常，由 core 异常处理器转换为 API 错误。
- 所有响应都应带 request_id。
- 所有 router 返回值必须通过 core response helpers 包装。
- 禁止业务 router 直接返回裸 dict、裸 list 或未封装 Pydantic schema。
