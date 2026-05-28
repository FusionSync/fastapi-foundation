# Core Tasks

## 职责

Tasks 模块负责异步任务抽象，用于长耗时处理、外部服务调用、批量导入导出和后台作业。

## 目录建议

```text
src/core/tasks/
  provider.py
  registry.py
  models.py
  repository.py
  sync.py
  rq.py
  celery.py
```

## Provider 策略

```text
sync
  本地开发和单机版，直接同步执行。

rq
  简单异步队列，适合 MVP。

celery
  复杂任务编排和生产部署。
```

## 任务类型

任务类型由 app 注册，例如：

```text
example.import
example.export
file.cleanup
report.generate
notification.send
```

## 任务状态

建议统一任务表：

```text
id
tenant_id
task_type
idempotency_key
status
progress
input_payload
result_payload
error_message
queue
attempt_count
max_attempts
created_at
started_at
finished_at
```

## 设计约束

- API 层只提交任务，不直接执行长耗时逻辑。
- 任务必须可重试。
- 任务必须声明幂等键或业务唯一键，避免 API 重试重复提交。
- 任务输出必须落库或落文件。
- 任务日志关联 request_id、tenant_id 和 task_id。
- worker 执行前必须检查 tenant lifecycle gate；`suspended/deleting` 租户按行为矩阵拒绝或跳过任务。
- 任务失败先进入 `failed`，重试达到 `max_attempts` 后进入 `dead_letter`，并提供 CLI 重试。
- outbox dispatcher 和 task worker 是不同进程角色；可靠任务提交优先通过 outbox 触发。

## 当前实现

第一版先提供轻量运行时接线，不绑定 Celery：

- `TaskRegistry.from_app_registry()` 从 `AppModule.task_handlers` 收集任务处理器。
- `TaskHandlerSpec.handler_path` 必须能 import 到 callable。
- task_type 全局唯一，重复注册启动前失败。
- `SyncTaskProvider` 用于 local/profile 和单机版，可同步执行普通函数或 async handler。
- `SyncTaskProvider.submit()` 执行前调用 tenant lifecycle gate，禁止 suspended/deleting 租户执行 task。
- `TaskRun` 定义统一任务运行记录，保存 input、result、error、queue、request_id、attempt_count、started_at、finished_at。
- `TaskRunRepository` 可注入 `SyncTaskProvider`；注入后同步任务会持久化 `running -> succeeded/failed/dead_letter` 状态，不注入时保持原有纯运行时模式。
- `SyncTaskProvider.retry()` 可基于已落库 `TaskRun` 重新执行同一个 `TaskEnvelope`，并复用 tenant lifecycle gate。
- `core tasks failed list` 输出 `failed/dead_letter` 任务；`core tasks failed retry --task-id <id> --yes` 显式重试注册过的任务处理器。
- scheduler 通过 `TaskEnvelope` 提交任务，不绕过 `SyncTaskProvider` 或未来队列 provider，因此计划触发、API 提交和 outbox 触发共享同一套租户 gate 和执行契约。

后续接入 RQ 或 Celery 时，provider 必须复用 `TaskEnvelope`、`TaskRegistry`、`TaskRun` 和 tenant gate，不允许业务 app 直接依赖具体队列实现。

## 运行角色

生产至少拆分：

```text
server             接收 API
worker             执行异步任务
scheduler          触发周期任务
outbox-dispatcher  发布可靠事件
migrate            执行迁移命令
```

每个角色必须有独立启动命令、并发配置、健康检查和优雅停机策略。
