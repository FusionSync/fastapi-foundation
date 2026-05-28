# Core Rate Limit

## 职责

Rate Limit 模块负责 API 限流、租户配额和防滥用控制。

## 目录建议

```text
src/core/rate_limit/
  provider.py
  rules.py
  middleware.py
  deps.py
```

## 限流维度

```text
global
tenant_id
user_id
ip_address
route
```

## 规则示例

```text
auth.login:
  5/minute/ip

files.upload:
  100/hour/tenant

default.write:
  300/minute/user
```

## 响应

默认触发限流时返回 `429 + RATE_LIMITED`，并带 `Retry-After`：

```json
{
  "code": "RATE_LIMITED",
  "message": "请求过于频繁",
  "data": null,
  "list": null,
  "pagination": null,
  "details": {
    "retry_after": 30
  },
  "request_id": "req_xxx"
}
```

只有显式启用 `API__ERROR_HTTP_STATUS_MODE=always_200` 时，兼容客户端才收到 HTTP 200；此时仍必须带 `Retry-After`、`X-App-Code=RATE_LIMITED` 和 `X-Request-ID`。

## 设计要求

- 限流依赖 Cache provider，不直接依赖 Redis。
- 支持按路由覆盖默认规则。
- 所有限流命中必须进入指标和审计。
- Redis 或 Cache provider 故障时必须有明确策略：认证、登录、支付、批量写入等高风险接口默认 fail-closed；普通读接口可按配置 fail-open 并记录告警。
