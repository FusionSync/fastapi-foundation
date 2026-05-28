# Core Tasks

## 职责

Tasks 模块负责异步任务抽象，用于长耗时处理、外部服务调用、批量导入导出和后台作业。

## 目录建议

```text
src/core/tasks/
  provider.py
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
- 任务失败超过上限进入 failed/dead-letter 状态，并提供 CLI 重试。
- outbox dispatcher 和 task worker 是不同进程角色；可靠任务提交优先通过 outbox 触发。

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
