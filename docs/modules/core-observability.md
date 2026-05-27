# Core Observability

## 职责

Observability 模块负责日志、指标、追踪和运行诊断。

## 目录建议

```text
src/core/observability/
  logging.py
  metrics.py
  tracing.py
  health.py
```

## 日志字段

```text
timestamp
level
message
request_id
tenant_id
user_id
route
method
status_code
duration_ms
```

## 健康检查

```text
GET /healthz
GET /readyz
GET /version
```

`healthz` 只检查进程存活，`readyz` 检查数据库、缓存、存储等依赖。

## 指标

第一版至少记录：

- HTTP 请求数和耗时。
- 任务执行数、耗时和失败数。
- 数据库连接状态。
- 存储访问失败数。

## 设计要求

- 日志必须结构化。
- 敏感字段必须脱敏。
- 每个请求必须有 request_id。
- 私有化部署也要能本地查看日志和健康状态。
