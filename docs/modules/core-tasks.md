# Core Tasks

## Progress

- Status: `partial`
- Done: task registry、sync provider、SQLAlchemy database queue provider、Celery task provider、Celery app/`core.tasks.execute` 执行入口、TaskRun 持久状态、repository、tenant 删除取消未完成任务、stale recovery、ack/retry/backoff/dead-letter、task CLI、scheduler 提交链路、worker 本地/数据库队列执行 loop、task trace_id handoff、task submit 幂等 mutation guard task_id 绑定 checkpoint、quota submit wrapper、队列部署 profile 参数和 worker heartbeat 已落地。
- Next: _none_

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

database
  通过 SQLAlchemy `TaskRun` 表实现等价队列 provider，适合 private/cloud 早期部署和无外部 broker 的私有化交付。

celery
  入库后投递 Celery broker；Celery worker 运行 `core.tasks.execute`，再按 TaskRun 执行已注册 handler。
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
next_retry_at
created_at
started_at
finished_at
```

## 设计约束

- API 层只提交任务，不直接执行长耗时逻辑。
- 任务必须可重试。
- 任务必须声明幂等键或业务唯一键，避免 API 重试重复提交。
- 同一 tenant 下同一 `idempotency_key` 只能创建一个 `TaskRun`；重复提交相同 task_type 和 payload 时返回已有 TaskRun 的结果，不再次执行 handler；同 key 但 task_type/payload 不一致时返回 `TASK_IDEMPOTENCY_KEY_CONFLICT`。
- 任务输出必须落库或落文件。
- 任务日志关联 request_id、trace_id、tenant_id 和 task_id。
- worker 执行前必须检查 tenant lifecycle gate；`suspended/deleting` 租户按行为矩阵拒绝或跳过任务。
- sync provider 任务失败先进入 `failed`，重试达到 `max_attempts` 后进入 `dead_letter`，并提供 CLI 重试。
- database queue provider 任务失败会 ack 当前尝试并按 `retry_backoff_seconds` 写回 `pending + next_retry_at`，达到 `max_attempts` 后进入 `dead_letter`。
- worker 崩溃留下的长期 `running` 任务必须有恢复入口；恢复时未达重试上限的任务进入 `failed`，已达上限的任务进入 `dead_letter`。
- tenant 进入删除/归档编排时，未完成任务必须可被标记为 `cancelled`，不再被 worker 领取或人工 retry。
- outbox dispatcher 和 task worker 是不同进程角色；可靠任务提交优先通过 outbox 触发。

## 当前实现

当前提供三类运行时接线：

- `TaskRegistry.from_app_registry()` 从 `AppModule.task_handlers` 收集任务处理器。
- `TaskHandlerSpec.handler_path` 必须能 import 到 callable。
- `check_app()` 会在启动期校验 task handler 签名必须接受一个 envelope 参数。
- task_type 全局唯一，重复注册启动前失败。
- `SyncTaskProvider` 用于 local/profile 和单机版，可同步执行普通函数或 async handler。
- `SyncTaskProvider.submit()` 执行前调用 tenant lifecycle gate，禁止 suspended/deleting 租户执行 task。
- `DatabaseQueueTaskProvider.submit()` 只把任务写为 `pending TaskRun`，不在 API/scheduler 提交路径直接执行业务 handler。
- `DatabaseQueueTaskProvider.run_next()` 由 worker 领取 `pending` 且 `next_retry_at` 到期的任务，执行后标记 `succeeded`；失败时按 backoff 重新入队或转 `dead_letter`。
- `CeleryTaskProvider.submit()` 只把任务写为 `pending TaskRun`，并向 Celery broker 投递 `core.tasks.execute(task_id=...)`；重复 idempotency 提交不会重复投递 Celery 消息。
- `create_celery_app()` 使用 `DEPENDENCIES__RABBITMQ_URL` 创建 Celery app，可选使用 `DEPENDENCIES__REDIS_URL` 作为 result backend。
- `run_persisted_task()` 是 Celery worker 执行入口，会按 `TaskRun` 重新构造 `TaskEnvelope` 并复用 `SyncTaskProvider.run_task_run()` 的租户 gate、handler 注册和结果落库逻辑。
- task handler 执行期间会从 `TaskEnvelope` 注入冻结背景上下文，透传 `request_id`、`trace_id` 和 `tenant_id`，避免继承外层 HTTP/CLI ContextVar。
- `TaskRun` 定义统一任务运行记录，保存 input、result、error、queue、request_id、trace_id、attempt_count、next_retry_at、started_at、finished_at。
- `TaskRunRepository` 可注入 `SyncTaskProvider`；注入后同步任务会持久化 `running -> succeeded/failed/dead_letter` 状态，不注入时保持原有纯运行时模式。
- `TaskRunRepository.start_once()` 使用 insert-first + 唯一约束处理 task idempotency；`SyncTaskProvider.submit()` 遇到 duplicate 时返回已有运行记录，不重新执行 handler。
- `TaskRunRepository.enqueue_once()` 使用同一唯一约束处理 database queue idempotency；重复提交 pending/running 任务返回已有 `TaskRun`，不创建第二条队列记录。
- `SyncTaskProvider.retry()` 可基于已落库 `TaskRun` 重新执行同一个 `TaskEnvelope`，并复用 tenant lifecycle gate。
- `TaskRunRepository.claim_next_pending()` 可按 queue 领取一个 `pending` 任务，跳过未到 `next_retry_at` 的任务，标记为 `running` 并递增 attempt_count。
- `SyncTaskProvider.run_task_run()` 可执行已持久化的 `pending/running/failed/dead_letter` 任务记录，复用 `TaskEnvelope`、注册 handler、tenant lifecycle gate 和结果落库逻辑。
- `core tasks failed list` 输出 `failed/dead_letter` 任务；`core tasks failed retry --task-id <id> --yes` 显式重试注册过的任务处理器。
- `TaskRunRepository.recover_stale_running()` 可把超过阈值的 `running` 任务恢复为 `failed/dead_letter`，避免 worker 崩溃后幂等键永久被占用。
- `TaskRunRepository.cancel_for_tenant()` 可把 tenant 下 `pending/running/failed` 任务标记为 `cancelled`，供租户删除/归档编排停止未完成后台任务。
- `core tasks running recover --older-than-seconds <n> --yes` 执行恢复，输出被恢复的任务列表。
- scheduler 通过 `TaskEnvelope` 提交任务，不绕过配置选中的 Tasks provider；local 默认 `sync`，private/cloud profile 默认 `database`，因此计划触发、API 提交和 outbox 触发共享同一套租户 gate 和执行契约。
- 需要配额控制的任务提交链路可用 `QuotaTaskSubmitter` 包装 `SyncTaskProvider` 或 `DatabaseQueueTaskProvider`；包装器在调用 provider 前 reserve，quota 不足时不提交任务，provider 抛错时释放 reservation。
- `core scheduler --run` 的 cron due loop 复用同一提交链路，scheduler 本身只构造 `TaskEnvelope` 并交给 task provider，触发后生成 `TaskRun` 并写入 `ScheduleTriggerLog`。
- `core worker --run-once` 加载 app task handler，按 queue 领取一个 `pending` `TaskRun`，执行后持久化为 `succeeded/failed/dead_letter`；当前用于 local/CI 有限轮验证，不替代生产级队列 worker。
- `core worker --run` 可按 `--max-iterations` 做有限轮验证，未设置时作为本地常驻 loop，空转时按 `--idle-sleep-seconds` 休眠。
- `core worker --run` / `--run-once` 支持 `--provider sync|database`、`--max-attempts` 和 `--retry-backoff-seconds`，profile 模板通过 `TASK_QUEUE__*` 环境变量统一参数；`provider=celery` 必须由 Celery worker 执行，core worker 会拒绝静默降级。
- `core worker --run --instance-id <id>` 每轮写入 `process_heartbeats`，details 包含 queue、iterations 和任务统计。

后续如果替换为其他队列 provider，仍必须复用 `TaskEnvelope`、`TaskRegistry`、`TaskRun` 和 tenant gate，不允许业务 app 直接依赖具体队列实现。

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
