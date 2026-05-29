# Core Events

## Progress

- Status: `connected`
- Done: event handler spec、event schema spec、schema/version 兼容校验、handler 错误分类、`EventRegistry`、`EventPublisher` 协议、outbox-backed publisher、outbox `dispatch-once` CLI、outbox-dispatcher run loop、handler 幂等分发、handler 外部 side-effect 幂等辅助 API 和跨模块事件契约示例已能从 app registry 汇总 handler 并投递 outbox event。
- Next: _none_

## 职责

Events 模块提供应用内事件总线，用于解耦业务动作和附加行为。需要可靠投递的事件必须走事务性 Outbox。

## 典型事件

- `tenant.created`
- `tenant.reactivated`
- `user.created`
- `file.uploaded`
- `task.completed`
- `resource.created`
- `resource.updated`
- `resource.deleted`

## 目录建议

```text
src/core/events/
  bus.py
  handlers.py
  types.py
  registry.py
```

## 使用场景

- 生成审计日志。
- 更新派生状态。
- 发送通知。
- 触发后续任务。

## 设计要求

- 进程内事件只允许用于非关键、可丢弃的轻量通知。
- 会影响审计、任务派发、权限投影、文件清理、外部通知的事件必须写入 Outbox。
- 事件处理失败不能回滚已提交业务事务，但必须进入重试、死信或人工处理流程。
- 事件 payload 必须包含 tenant_id 和 actor_id。
- 事件命名、payload schema 和版本必须注册。
- 事务性 Outbox 细则见 [Transactional Outbox](core-outbox.md)。

## 当前实现

第一版提供轻量 `EventRegistry`：

- app 通过 `AppModule.event_handlers` 声明事件处理器。
- app 可通过 `AppModule.event_schemas` 声明事件 schema：`required_payload_fields` 定义除 `tenant_id`、`actor_id`、`request_id` 外的必填字段，`field_types` 支持 `str`、`int`、`float`、`number`、`bool`、`dict` 和 `list`。
- schema 新版本通过 `compatible_with` 声明兼容的旧版本；兼容版本必须已注册，且不能移除旧版本必填字段或改变已声明字段类型。
- `EventRegistry.from_app_registry()` 统一导入 handler 并注册。
- `EventRegistry.validate_event()` 在 outbox 写入和 dispatcher 投递前校验 event_type、event_version、tenant_id 和 payload schema；schema-only event 可被写入 outbox，dispatcher 仍要求存在对应 handler 才能投递。
- `EventHandlerSpec.handler_path` 必须能 import 到 callable。
- `check_app()` 会在启动期校验 event handler 签名必须接受一个 envelope 参数。
- `register_spec()` 使用 `handler_path` 作为稳定 handler key；直接 `register()` 的 handler key 默认为 `module.qualname`。
- 同一 event_type/event_version 可以注册多个 handler。
- 同一 event_type/event_version/handler_key 重复注册会启动前失败，避免重复副作用。
- handler 默认异常按 transient 处理，继续走 outbox retry/max_attempts；handler 可抛出 `EventHandlerPermanentError` 表示不可重试错误，dispatcher 会直接 dead-letter。payload schema 错误使用同一 permanent 分类，避免坏事件反复重试。
- 业务 service 依赖 `EventPublisher.publish()`，不直接依赖 `OutboxRepository.add()`；当前可靠实现是 `OutboxEventPublisher`。
- `OutboxEventPublisher` 通过 `OutboxRepository.add()` 写入 outbox，并使用同一个 registry 校验 event_type/event_version 是否已注册。
- `core outbox dispatch-once` 和 `core outbox-dispatcher --run` 会按 `--installed-app` 或 settings 加载 `EventRegistry`，领取 outbox event 并调用已注册 handler。
- outbox dispatcher 会向 `EventRegistry.dispatch()` 传入 `IdempotencyStore`，以 `event_id + handler_key` 跳过已成功 handler，并允许失败 handler 后续重试。
- outbox handler 内部可用 `run_event_side_effect(effect_key, effect, request_payload=...)` 包裹外部 HTTP、消息、邮件或第三方 API 调用；该 helper 使用当前 dispatcher 的 `IdempotencyStore`，以 `event_id + handler_key + effect_key` 记录已完成 side effect。
- outbox handler 执行期间会从 `EventEnvelope` 注入冻结背景上下文，包含 payload 中的 `request_id/actor_id`、tenant_id 和 `outbox:{event_type}:v{event_version}` route，执行后 reset。
- `core.cache.register_cache_invalidation_handlers()` 可把权限事实、租户生命周期和租户成员关系缓存失效 handler 注册进同一 `EventRegistry`，由 outbox dispatcher 可靠触发。

需要可靠投递的事件仍然通过 outbox 写入和 dispatcher 投递；`EventRegistry` 只负责运行时 handler 解析和分发，不承担消息队列职责。

## Cross-module Contract Example

跨模块事件必须把生产者 schema 和消费者 handler 都写入 `AppModule`，示例见：

- `docs/contracts/events/cross-module-side-effect.md`

关键约束：

- producer app 声明 `EventSchemaSpec`，明确 event type、version、必填 payload 和字段类型。
- consumer app 声明 `EventHandlerSpec`，handler path 必须可导入且签名只接受一个 `EventEnvelope`。
- payload 必须包含 `tenant_id`、`actor_id` 和 `request_id`，有 trace 时带 `trace_id`。
- handler 对外部系统的每一次副作用都必须有稳定 `effect_key`，并通过 `run_event_side_effect()` 执行。
- `run_event_side_effect()` 只能在 outbox dispatcher 调用的 handler 内使用；进程内临时事件没有持久 idempotency context，不允许承载外部关键副作用。
