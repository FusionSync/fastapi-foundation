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
status
progress
input_payload
result_payload
error_message
created_at
started_at
finished_at
```

## 设计约束

- API 层只提交任务，不直接执行长耗时逻辑。
- 任务必须可重试。
- 任务输出必须落库或落文件。
- 任务日志关联 request_id、tenant_id 和 task_id。
