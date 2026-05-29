# Core Idempotency

## Progress

- Status: `connected`
- Done: 持久 `IdempotencyRecord`、key builder、原子 insert-and-claim、状态流转 store、response replay/cache 语义、过期清理命令、冲突诊断命令、outbox handler 执行幂等复用，以及 accounts/files/tasks 高风险写操作的可复用 mutation guard 已落地。
- Next: _none_

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
- `diagnose()` 可按 tenant/user/route/key/request_hash 判断记录是 missing、replayable、request_hash_conflict、in_progress、failed 需显式重试，还是 expired 后可复用。
- 可通过 `outbox_event_id` 或 `task_id` 绑定异步副作用，避免客户端重试重复提交。
- outbox dispatcher 复用 `IdempotencyStore` 保护 handler 执行，范围为 `tenant_id + actor_id + outbox route + event_id`，其中 route 包含 `event_type`、`event_version` 和 `handler_key`。
- `locked_until` 过期后允许重新领取；`expires_at` 过期后允许复用同一 key。
- `core idempotency expire --yes --json` 会把过期记录标记为 `expired` 并清理 `locked_until`。
- `core idempotency diagnose --tenant-id ... --user-id ... --route ... --idempotency-key ... --request-hash ... --json` 输出稳定诊断 JSON；`replayable` 结果会包含可直接返回的 `response_code`、`response_body`、`task_id` 和 `outbox_event_id`。
- `IdempotencyMutationGuard.run()` 封装高风险写操作的显式调用模式：首次请求 claim 后执行 handler，保存 response；重复请求直接 replay 已保存 response，不再次创建 account、写 file storage 或提交 task。
- guard 可通过 `task_id_builder` / `outbox_event_id_builder` 将异步副作用绑定到幂等记录，客户端重试时返回同一 `task_id` / `outbox_event_id`。
- handler 抛错时 guard 会把记录标记为 `failed`，默认不自动重试；需要重试的接口必须显式设置 `retry_failed=true`。

当前没有直接做全局 HTTP middleware。高风险写接口应在 service/route 入口显式调用 `IdempotencyMutationGuard`，避免无意覆盖低风险读接口或非幂等业务语义。
