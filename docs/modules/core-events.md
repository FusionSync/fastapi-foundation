# Core Events

## 职责

Events 模块提供应用内事件总线，用于解耦业务动作和附加行为。需要可靠投递的事件必须走事务性 Outbox。

## 典型事件

- `tenant.created`
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
- 同一 event_type/event_version 可以注册多个 handler。
- 同一 event_type/event_version/handler_path 重复注册会启动前失败，避免重复副作用。
- `OutboxRepository.add()` 可使用同一个 registry 校验 event_type/event_version 是否已注册。

需要可靠投递的事件仍然通过 outbox 写入和 dispatcher 投递；`EventRegistry` 只负责运行时 handler 解析和分发，不承担消息队列职责。
