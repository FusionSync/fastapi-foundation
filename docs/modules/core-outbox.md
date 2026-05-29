# Core Transactional Outbox

## Progress

- Status: `connected`
- Done: outbox model、repository、outbox-backed publisher、同事务写入、条件领取、一次性 dispatcher CLI、outbox-dispatcher run loop、shutdown signal、profile batch/sleep 参数、process heartbeat、有限重试、dead-letter replay、lease 完成校验、handler trace_id handoff、handler schema/version 校验、handler 幂等执行保护、handler 外部 side-effect 幂等辅助 API 和可选跨进程 dispatcher lock 已落地。
- Next: _none_

## 为什么需要 Outbox

普通事件发布有两个典型风险：

```text
场景 A：
  1. 写入业务数据成功
  2. 进程崩溃
  3. 事件还没发出去
  结果：业务数据存在，但下游任务/审计/通知丢失

场景 B：
  1. 事件先发出去了
  2. 数据库事务回滚
  结果：下游收到一个实际上不存在的数据变更
```

Transactional Outbox 的做法是：**业务数据和事件记录写入同一个数据库事务**。事务提交后，后台 dispatcher 再异步读取 outbox 表并投递事件。

本项目的 outbox 只做可靠副作用的最小闭环，不设计成复杂消息平台。第一版目标是：

- 同事务落库。
- 简单状态机。
- 多 worker 不重复领取。
- 失败有限重试。
- 死信可重放。
- handler 以 `event_id` 幂等。

## 核心流程

```text
service 开启数据库事务
  -> 写业务表
  -> 写 outbox_events 表
  -> 提交事务

outbox dispatcher
  -> 扫描 pending 事件
  -> 加锁领取事件
  -> 投递到事件处理器/消息队列
  -> 成功后标记 published
  -> 失败后重试或进入 dead letter
```

这样可以保证：

- 业务写入成功，事件一定有记录。
- 业务写入回滚，事件也回滚。
- 投递失败可以重试。
- 进程崩溃后可以恢复扫描。

## 目录建议

```text
src/core/outbox/
  models.py
  repository.py
  dispatcher.py
  replay.py
  handlers.py
  registry.py
```

## 事务边界

outbox 必须通过 core transaction/unit-of-work 写入：

```text
async with unit_of_work() as uow:
  await resource_repo.create(..., session=uow.session)
  await event_publisher.publish(...)
```

规则：

- `OutboxEventPublisher` 内部的 repository 必须绑定同一个 `AsyncSession`。
- `outbox_repo.add()` 禁止在已有事务外隐式打开新连接。
- rollback 后不得留下 outbox event。
- 后台任务没有 HTTP request 时，必须显式传入 TaskContext 和 unit-of-work。
- nested transaction 第一版不做复杂编排；遇到嵌套调用时复用外层 unit-of-work。

## outbox_events 表

```text
id
tenant_id
event_type
event_version
aggregate_type
aggregate_id
payload
status
claim_version
attempt_count
max_attempts
next_retry_at
locked_by
locked_until
last_error
published_at
dead_letter_reason
created_at
```

## 状态机

```text
pending
  -> publishing
  -> published
  -> failed
  -> dead_letter
```

状态说明：

- `pending`：等待领取。
- `publishing`：某个 dispatcher 正在处理。
- `published`：处理成功。
- `failed`：本次处理失败，等待 `next_retry_at` 后重试。
- `dead_letter`：超过最大重试次数，需要人工或 CLI 重放。

## 写入规则

service 不直接发可靠事件，而是在事务中写 outbox：

```text
await service.run_in_transaction(
  write_business_data()
  event_publisher.publish(event)
)
```

要求：

- outbox 写入必须与业务数据使用同一个事务连接。
- event payload 必须包含 `tenant_id`、`actor_id`、`request_id`；存在 `trace_id` 时 dispatcher 必须透传给 handler 背景上下文。
- event_type 和 event_version 必须注册；如果 registry 中存在 `EventSchemaSpec`，写入时还会校验 schema 必填字段、字段类型和 tenant_id 一致性。
- handler 必须以 `event_id` 做幂等；复杂业务可以额外使用业务唯一键。
- handler 内部调用外部系统时必须用 `run_event_side_effect()` 或外部系统自己的幂等键保护每一次副作用。
- 不要求第一版支持复杂事件溯源、全局顺序或跨服务 exactly-once。

## 投递规则

dispatcher 需要：

- 支持批量领取。
- 使用条件更新领取事件，条件至少包含 `status in (pending, failed)`、`next_retry_at <= now`、`locked_until is null or locked_until < now`。
- PostgreSQL profile 可使用 `FOR UPDATE SKIP LOCKED` 优化领取；SQLite/local profile 可使用单 worker。
- 领取成功后设置 `status=publishing`、`locked_by`、`locked_until`，并递增 `claim_version` 作为 fencing token。
- 标记 `published` 或 `failed/dead_letter` 时必须使用条件更新再次校验 dispatcher lease：事件仍为 `publishing`、`locked_by` 等于当前 dispatcher、`claim_version` 等于领取时的 token，并且 `locked_until` 仍未过期；未领取、已完成、死信、非当前 dispatcher 持有、锁已过期或已被重新领取的事件不得被完成。
- `OutboxDispatcher` 可注入 `LockProvider`，在每轮批量领取前先获取默认 `outbox:dispatch` 锁；获取失败时返回 0 claimed/published/failed/dead_lettered 且不领取事件。
- dispatcher 跨进程锁只是减少多实例同时扫描的粗粒度保护，不能替代 `outbox_events` 条件领取、`claim_version` fencing 或 handler 幂等记录。
- 调用 handler 前通过 `IdempotencyStore` 以 `event_id + handler_key` 记录 handler 执行结果；已成功的 handler replay 时跳过，失败记录允许后续重试重新领取。
- 调用 handler 前会再次通过 `EventRegistry.validate_event()` 校验 schema/version，防止 schema 变更后历史坏事件反复调用 handler；schema 错误按 permanent failure 直接进入 dead letter。
- handler 未分类异常默认按 transient failure 重试；明确抛出 `EventHandlerPermanentError` 时直接 dead-letter，并在 `last_error` / `dead_letter_reason` 中保留 `permanent` 分类。
- 支持指数退避或固定退避。
- 达到最大重试后进入 dead letter。
- 提供 dead letter 重放命令。
- 可注入 `MetricsRegistry`，每次 `dispatch_once()` 后记录 claimed/published/failed/dead_lettered outcome，并刷新 pending/publishing/dead_letter gauge。

崩溃恢复：

- dispatcher 崩溃后，`locked_until` 到期的 `publishing` 事件可重新领取。
- 如果 handler 已成功但事件未标记 `published`，dispatcher replay 会通过 handler 幂等记录跳过已成功 handler。
- 如果某个外部副作用已成功但 handler 后续失败，`run_event_side_effect()` 的记录会让下一次 retry 跳过该副作用并复用已记录 response。
- 如果外部副作用已经执行但进程在 helper 标记成功前崩溃，handler 仍必须把 `event_id` 传给外部系统作为幂等键，或依赖业务唯一约束兜底，避免重复副作用。
- 第一版不追求 exactly-once；目标是 at-least-once delivery + idempotent handler。

## Handler External Side Effects

标准写法：

```python
from core.events import EventEnvelope, run_event_side_effect


async def notify_vendor(envelope: EventEnvelope) -> None:
    await run_event_side_effect(
        "vendor.notify",
        lambda: vendor_client.send(
            idempotency_key=envelope.event_id,
            tenant_id=envelope.tenant_id,
            aggregate_id=envelope.aggregate_id,
        ),
        request_payload={"aggregate_id": envelope.aggregate_id},
    )
```

运行规则：

- `effect_key` 必须在同一个 handler 内稳定且唯一，例如 `crm.tenant.upsert`、`email.welcome.send`。
- helper 使用当前 dispatcher 注入的 `IdempotencyStore`，记录维度为 tenant、actor、event、handler 和 effect。
- side effect 成功后即写入 succeeded 记录；如果 handler 后续失败，下一次 outbox retry 会 replay 该记录，不再调用外部系统。
- side effect 函数抛错时记录 failed，并随 handler 失败走 outbox retry 或 dead-letter。
- helper 不替代外部系统幂等键。对 HTTP、消息队列、邮件、支付、CRM 等外部系统，仍应把 `event_id` 或业务唯一键传过去。

## Outbox CLI

已提供最小运维闭环：

```bash
core outbox dispatch-once --installed-app apps.example_domain.module --database-url sqlite+aiosqlite:///./data/local.db --json
core outbox-dispatcher --run --installed-app apps.example_domain.module --database-url sqlite+aiosqlite:///./data/local.db --json
core outbox dead-letter list --database-url sqlite+aiosqlite:///./data/local.db --json
core outbox dead-letter replay --event-id <event_id> --database-url sqlite+aiosqlite:///./data/local.db --yes --json
```

行为：

- `dispatch-once` 通过 `--installed-app` 或 settings 加载 app 事件处理器，领取一批待投递事件，调用 handler，并输出 claimed/published/failed/dead_lettered。
- `outbox-dispatcher --run` 复用同一个运行层；默认持续循环，`--max-iterations` 可限制轮数，便于 CI 和本地 smoke。
- `outbox-dispatcher --run` 在 CLI 层安装 SIGTERM/SIGINT handler，向运行层传入 shutdown event；收到关闭信号时在当前轮处理完成后退出，并在结果中标记 `shutdown_requested`。
- 部署 profile 暴露 `OUTBOX_DISPATCHER__BATCH_SIZE` 和 `OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS`，进程模板会传给 `--batch-size` 和 `--idle-sleep-seconds`。
- `outbox-dispatcher --run --instance-id <id>` 每轮写入 `process_heartbeats`，details 包含 dispatcher_id、iterations 和投递统计。
- `list` 输出 `dead_letter` 事件的稳定 JSON，包含 tenant、event type、aggregate、attempt、last_error 和 dead_letter_reason。
- `replay` 必须显式传 `--yes`，避免误操作。
- `replay` 只允许重放 `dead_letter` 事件，成功后把状态改回 `pending`，清理 `dead_letter_reason`、`last_error`、`next_retry_at` 和锁字段。
- CLI 不直接执行 handler；重放后的事件仍由 outbox dispatcher 按正常领取规则处理。

## 与审计的关系

安全关键审计可以强一致写审计表；一般派生审计可以通过 Outbox 异步写入。

必须强一致的审计：

- 权限拒绝。
- 管理员配置变更。
- 跨租户访问。
- 安全策略变更。

可以异步的事件：

- 通知发送。
- 派生统计。
- 缓存刷新。
- 非关键 Webhook。
