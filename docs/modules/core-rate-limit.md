# Core Rate Limit

## Progress

- Status: `connected`
- Done: rate-limit provider、sliding-window limiter、规则、request middleware、标准 `Retry-After` 输出和基础 contract tests 已落地。
- Next: 无。

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

已落地 `RateLimitRule`、`RateLimitIdentity`、`RateLimitRegistry`、`CacheRateLimiter`、`SlidingWindowRateLimiter` 和 `RateLimitMiddleware`：

- `RateLimitRule` 声明 `name`、`limit`、`window_seconds`、`dimensions` 和 `fail_closed`。
- `RateLimitIdentity` 支持 `tenant_id`、`user_id`、`ip_address`、`route` 和 `global` 维度。
- `RateLimitRegistry` 支持默认规则和 route override，便于登录、上传、批量写入等接口单独调小阈值。
- `CacheRateLimiter` 使用 `CacheProvider.incr()` 实现 fixed-window 计数，不直接依赖 Redis。
- `SlidingWindowRateLimiter` 使用当前窗口和上一窗口的加权计数实现滑动窗口；它仍只依赖 `CacheProvider`，private/cloud 可用 `RedisCacheProvider` 提供跨进程计数。
- 超限返回 `RateLimitDecision(allowed=False)`；`require()` 抛 `RATE_LIMITED`，并带 `Retry-After` header 和稳定 details。
- `RateLimitMiddleware` 已接入 app factory。默认未配置 `app.state.rate_limit_registry` 和 `app.state.rate_limiter` 时无行为；配置后按 `METHOD path` 解析规则并在超限时直接返回统一 envelope。
- middleware 输出 `429 + RATE_LIMITED`，兼容模式下按 `API__ERROR_HTTP_STATUS_MODE=always_200` 返回 HTTP 200，但仍保留 `Retry-After`、`X-App-Code` 和 `X-Request-ID`。
- 命中限流时会写可选 `MetricsRegistry` 的 `rate_limit_hits_total{reason,route,rule}`，并调用可选 `AuditRecorder` 写 `rate_limit.hit` 审计。
- cache provider 故障时按 rule 决定 fail-open 或 fail-closed；高风险接口应使用默认 `fail_closed=True`。
- 指标契约已预留 `rate_limit_hits_total`。

第一版 middleware 适合 `global`、`ip_address`、`route` 和可由请求头/上下文得到的 `tenant_id` 维度。需要认证后 `user_id` 的细粒度策略，应在 route dependency 或 service gate 中复用 `CacheRateLimiter`，避免在认证上下文尚未建立前错误拒绝请求。
