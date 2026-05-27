# Core Events

## 职责

Events 模块提供应用内事件总线，用于解耦业务动作和附加行为。

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
```

## 使用场景

- 生成审计日志。
- 更新派生状态。
- 发送通知。
- 触发后续任务。

## 设计要求

- 第一版可以是进程内事件。
- 事件处理失败不能影响主事务，除非明确标记为强一致。
- 事件 payload 必须包含 tenant_id 和 actor_id。
- 后续可替换为 Redis Stream、消息队列或 Outbox Pattern。
