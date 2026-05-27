# Core Scheduler

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
