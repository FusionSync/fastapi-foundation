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

## 当前实现

已落地 `RateLimitRule`、`RateLimitIdentity`、`RateLimitRegistry` 和 `CacheRateLimiter`：

- `RateLimitRule` 声明 `name`、`limit`、`window_seconds`、`dimensions` 和 `fail_closed`。
- `RateLimitIdentity` 支持 `tenant_id`、`user_id`、`ip_address`、`route` 和 `global` 维度。
- `RateLimitRegistry` 支持默认规则和 route override，便于登录、上传、批量写入等接口单独调小阈值。
- `CacheRateLimiter` 使用 `CacheProvider.incr()` 实现 fixed-window 计数，不直接依赖 Redis。
- 超限返回 `RateLimitDecision(allowed=False)`；`require()` 抛 `RATE_LIMITED`，并带 `Retry-After` header 和稳定 details。
- 命中限流时会写可选 `MetricsRegistry` 的 `rate_limit_hits_total{reason,route,rule}`，并调用可选 `AuditRecorder` 写 `rate_limit.hit` 审计。
- cache provider 故障时按 rule 决定 fail-open 或 fail-closed；高风险接口应使用默认 `fail_closed=True`。
- 指标契约已预留 `rate_limit_hits_total`。

第一版没有直接做 middleware。业务 route 可以先显式调用 `RateLimitRegistry.resolve()` 和 `CacheRateLimiter.require()`；等核心 API dependency 形态稳定后，再抽成统一 dependency/middleware。
