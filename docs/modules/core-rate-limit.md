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

触发限流时仍返回 HTTP 200：

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

## 设计要求

- 限流依赖 Cache provider，不直接依赖 Redis。
- 支持按路由覆盖默认规则。
- 所有限流命中必须进入指标和审计。
