# Core HTTP Clients

## Progress

- Status: `partial`
- Done: resilient HTTP client、retry config、错误类型和 transport 抽象已落地。
- Next:
  - [ ] 接 timeout budget、metrics 和 trace propagation。
  - [ ] 增加按外部服务声明 credential/secret 的 provider 契约。

## 职责

HTTP Clients 模块负责统一外部 HTTP 调用，避免各 app 直接使用 `httpx` 或 `requests` 并散落 timeout、retry、trace 和错误处理。

## 目录建议

```text
src/core/http_clients/
  client.py
  config.py
  retry.py
  errors.py
```

## 核心能力

- 统一 timeout。
- 统一 retry 和 backoff。
- 自动透传 request_id、trace_id。
- 统一 User-Agent。
- 统一错误转换为 `ExternalServiceAppError`。
- 支持 mock client 便于测试。

## 使用场景

- OIDC/SSO 服务。
- 短信、邮件、Webhook。
- 外部文件服务。
- 外部 AI/模型服务。
- 其他内部微服务。

## 设计要求

- app 不直接创建裸 `httpx.AsyncClient`。
- 每个外部服务要有命名 client 和独立配置。
- 默认必须设置 timeout，禁止无限等待。
- 外部调用失败必须带服务名、请求 ID 和脱敏后的错误详情。

## 当前实现

已落地 `CoreHttpClient`、`HttpClientConfig`、`RetryConfig`、`ExternalServiceAppError` 和 transport 抽象：

- `HttpClientConfig` 要求 `service_name`、`base_url` 和正数 `timeout_seconds`，默认 timeout 为 5 秒。
- `CoreHttpClient` 自动注入 `User-Agent`、`X-Request-ID` 和 `X-Trace-ID`。
- `RetryConfig` 支持按状态码重试和 transport 异常重试，默认只尝试一次。
- HTTP 4xx/5xx 或 transport 异常会转换为 `EXTERNAL_SERVICE_ERROR`，HTTP status 为 502。
- 错误 details 包含 service、method、url、request_id、upstream status 或 error type，并通过 `redact_sensitive_data()` 脱敏 request/response body。
- `MockHttpTransport` 可记录请求并按脚本返回响应或异常，方便 app contract/integration 测试。
- 指标契约已预留 `external_http_requests_total`。

第一版没有直接绑定 `httpx`，而是先固定 core 侧 transport 协议。后续接真实 `HttpxTransport` 时，业务 app 不需要改变调用方式。
