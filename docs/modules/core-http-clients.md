# Core HTTP Clients

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
