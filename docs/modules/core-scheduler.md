# Core Scheduler

## Progress

- Status: `connected`
- Done: schedule registry、provider、trigger log repository、锁保护的触发路径、scheduler 背景上下文 handoff 和 `core scheduler --run-once` 本地运行入口已落地。
- Next:
  - [ ] 接 cron 持久化配置和后台 scheduler loop。
  - [ ] 将调度任务与 audit gate 和部署 profile 运行参数串通。

## 职责

Scheduler 模块负责“何时触发任务”。它不负责具体任务怎么执行，具体执行交给 Tasks 模块。

## 与 Celery 的关系

Celery 可以实现 scheduler，但 core 不应直接绑定 Celery。

```text
Scheduler
  定义触发时间、周期、错过触发策略、启停和注册。

Tasks
  定义任务提交、执行、状态、重试和结果。

Celery Beat
  可以作为 Scheduler provider。

Celery Worker
  可以作为 Tasks provider。
```

如果项目选择 Celery 技术栈，推荐组合是：

```text
core.scheduler provider = celery_beat
core.tasks provider = celery
```

本地开发或单机版可以使用：

```text
core.scheduler provider = apscheduler
core.tasks provider = sync
```

## 目录建议

```text
src/core/scheduler/
  provider.py
  registry.py
  apscheduler.py
  celery_beat.py
```

## 调度类型

```text
interval
cron
date
manual
```

## 使用场景

- 清理临时文件。
- 归档审计日志。
- 刷新外部配置缓存。
- 生成周期报表。
- 扫描超时任务。

## 设计要求

- 调度器只负责触发，不直接写业务逻辑。
- 分布式部署时必须保证同一调度不会多实例重复触发。
- 可以通过 Locks 模块保护单例调度。
- app 通过 module 注册 schedule definitions。
- 生产环境需要能查看调度状态和最近执行记录。
- schedule trigger 必须记录 `schedule_id`、`planned_at`、`triggered_at`、`task_id`、`status`。
- 错过触发策略必须显式声明：`skip`、`run_once` 或 `catch_up_limited`。
- 周期任务提交必须绑定幂等键，例如 `schedule_id + planned_at`。
- scheduler provider 可注入 `ScheduleTriggerRepository` 写 `ScheduleTriggerLog`；同一 `schedule_id + tenant_id + planned_at` 只能保留一条触发历史，重复触发返回已有历史并依赖 task idempotency 避免重复执行。
- scheduler 只提交任务或写 outbox，不直接执行业务逻辑。

## 当前实现

第一版先提供 `ScheduleRegistry`：

- 从 `AppModule.schedules` 收集 schedule definition。
- schedule_id 全局唯一。
- 每个 schedule 的 task_type 必须能在 `TaskRegistry` 中找到。
- `ManualScheduleProvider` 提供本地/运维触发入口，读取 `ScheduleRegistry`，构造带 `schedule_id + tenant_id + planned_at` 幂等键的 `TaskEnvelope`，再提交给 Tasks provider。
- `LockedScheduleProvider` 可包装任意 scheduler provider，在触发前获取 `scheduler:trigger:{schedule_id}:{tenant_id}:{planned_at}` 锁，触发完成或失败后释放锁，避免同一实例集内重复触发同一 planned slot。
- `ScheduleTriggerLog` 保存 `schedule_id`、`tenant_id`、`planned_at`、`triggered_at`、`task_id`、`task_type`、`status`、`request_id` 和错误信息。
- `ScheduleTriggerRepository.record_result()` 使用 insert-first + 唯一约束记录触发历史；重复 trigger key 返回 `replayed`，不创建第二条历史。
- scheduler provider 不直接调用业务函数，tenant lifecycle gate 仍由 task provider 执行。
- scheduler trigger 执行期间会从 `ScheduleTriggerRequest` 注入冻结背景上下文，避免继承外层 HTTP/CLI ContextVar。
- `core scheduler --run-once` 可按 `--installed-app` 加载 `ScheduleRegistry`/`TaskRegistry`，用 local `SyncTaskProvider` 触发指定 schedule，写入 `TaskRun` 和 `ScheduleTriggerLog`，并输出稳定 JSON。

后续 APScheduler 或 Celery Beat provider 必须读取同一份 `ScheduleRegistry`，复用 `ScheduleTriggerRequest`/`ScheduleTriggerResult` 语义，并把触发结果提交到 Tasks provider，而不是直接调用业务函数。private/cloud 多实例部署时，`LockedScheduleProvider` 必须注入 Redis、数据库 advisory lock 或等价的分布式 `LockProvider`；内存 lock 只适合 local/profile 和测试。
