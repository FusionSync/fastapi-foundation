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
  models.py
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
status
response_code
response_body
task_id
outbox_event_id
locked_until
expires_at
```

状态机：

```text
processing
  -> succeeded
  -> failed
  -> expired
```

## 设计要求

- 幂等 key 必须绑定 tenant、user 和 route。
- 同 key 但 request body 不一致时返回 `IDEMPOTENCY_KEY_CONFLICT`。
- 创建记录必须使用唯一约束和原子 insert-and-claim，不能先查后写。
- 同 key 请求仍在 `processing` 时，默认返回 `409 + IDEMPOTENCY_IN_PROGRESS` 或按接口声明等待短轮询。
- `succeeded` 请求再次到达时返回第一次的 response_code 和 response_body。
- `failed` 是否允许重试必须由接口声明，默认高风险写操作不自动重试。
- 幂等记录必须有 TTL。
- 对高风险写接口，router 应显式声明是否需要幂等。
- 提交任务或写 outbox 时，幂等记录必须绑定 `task_id` 或 `outbox_event_id`，避免客户端重试重复提交。

## 当前实现

已落地 `IdempotencyRecord` 和 `IdempotencyStore`：

- 使用 `tenant_id + user_id + route + idempotency_key` 唯一约束绑定幂等范围。
- `claim()` 采用 insert-first + 唯一约束冲突处理，避免先查再插的并发窗口。
- 首次 claim 创建 `processing` 记录，并写入 `locked_until` 和 `expires_at`。
- 相同 key、相同请求仍在处理中时返回 `IDEMPOTENCY_IN_PROGRESS`。
- 相同 key、不同请求指纹返回 `IDEMPOTENCY_KEY_CONFLICT`。
- 成功请求通过 `mark_succeeded()` 保存 `response_code` 和 `response_body`，后续重复请求返回原响应。
- 可通过 `outbox_event_id` 或 `task_id` 绑定异步副作用，避免客户端重试重复提交。
- `locked_until` 过期后允许重新领取；`expires_at` 过期后允许复用同一 key。

第一版没有直接做 HTTP middleware。推荐先由高风险写接口在 service/route 入口显式调用 store，等账户、文件、任务等大功能全部连通后，再抽象成可复用 dependency。
