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
