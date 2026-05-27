# Core Idempotency

## 职责

Idempotency 模块负责处理重复提交和客户端重试，保证同一个幂等请求不会重复创建资源或重复触发任务。

## 与 Locks 的区别

```text
Locks
  解决并发执行，同一时间只能有一个执行者。

Idempotency
  解决重复请求，同一个业务请求多次到达时返回同一结果。
```

两者经常配合使用，但不能混为一层。

## 目录建议

```text
src/core/idempotency/
  keys.py
  store.py
  middleware.py
  deps.py
```

## 使用场景

- 创建资源。
- 文件上传完成确认。
- 提交后台任务。
- 外部支付、通知、回调。
- 客户端超时后的自动重试。

## Key 设计

客户端可通过 header 传入：

```text
Idempotency-Key: uuid-or-client-generated-key
```

服务端存储：

```text
tenant_id
user_id
route
idempotency_key
request_hash
response_code
response_body
expires_at
```

## 设计要求

- 幂等 key 必须绑定 tenant、user 和 route。
- 同 key 但 request body 不一致时返回 `IDEMPOTENCY_KEY_CONFLICT`。
- 幂等记录必须有 TTL。
- 对高风险写接口，router 应显式声明是否需要幂等。
