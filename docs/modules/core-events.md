# Core Events

## Progress

- Status: `connected`
- Done: event handler spec、`EventRegistry`、`EventPublisher` 协议、outbox-backed publisher、outbox `dispatch-once` CLI、outbox-dispatcher run loop 和 handler 幂等分发已能从 app registry 汇总 handler 并投递 outbox event。
- Next:
  - [ ] 定义事件 schema/version 兼容策略和 handler 错误分类语义。
  - [ ] 补充跨模块事件契约示例和 handler 外部 side-effect 指南。

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
- `EventRegistry.from_app_registry()` 统一导入 handler 并注册。
- `EventHandlerSpec.handler_path` 必须能 import 到 callable。
- `register_spec()` 使用 `handler_path` 作为稳定 handler key；直接 `register()` 的 handler key 默认为 `module.qualname`。
- 同一 event_type/event_version 可以注册多个 handler。
- 同一 event_type/event_version/handler_key 重复注册会启动前失败，避免重复副作用。
- 业务 service 依赖 `EventPublisher.publish()`，不直接依赖 `OutboxRepository.add()`；当前可靠实现是 `OutboxEventPublisher`。
- `OutboxEventPublisher` 通过 `OutboxRepository.add()` 写入 outbox，并使用同一个 registry 校验 event_type/event_version 是否已注册。
- `core outbox dispatch-once` 和 `core outbox-dispatcher --run` 会按 `--installed-app` 或 settings 加载 `EventRegistry`，领取 outbox event 并调用已注册 handler。
- outbox dispatcher 会向 `EventRegistry.dispatch()` 传入 `IdempotencyStore`，以 `event_id + handler_key` 跳过已成功 handler，并允许失败 handler 后续重试。

需要可靠投递的事件仍然通过 outbox 写入和 dispatcher 投递；`EventRegistry` 只负责运行时 handler 解析和分发，不承担消息队列职责。
