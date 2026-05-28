# Core Observability

## 职责

Observability 模块负责日志、指标、追踪和运行诊断。

## 目录建议

```text
src/core/observability/
  logging.py
  metrics.py
  middleware.py
  tracing.py
  health.py
```

## 日志字段

```text
timestamp
level
message
request_id
trace_id
tenant_id
user_id
route
method
status_code
app_code
duration_ms
deployment_mode
service_role
instance_id
version
```

## 健康检查

```text
GET /healthz
GET /readyz
GET /version
GET /metrics
```

`healthz` 只检查进程存活，`readyz` 返回统一 readiness envelope，当前覆盖 config、database URL、数据库可连接性、AppRegistry 和 MetricsRegistry。`readyz` 不通过时必须返回 HTTP 503，避免平台探针把不可服务实例加入流量。
worker、scheduler 和 outbox-dispatcher 也必须提供等价探针或 CLI health check。
非 HTTP 角色通过 `process_heartbeats` 保存最近一次心跳事实；健康检查读取最新
`ProcessHeartbeatSnapshot` 后，按角色匹配、状态和 freshness 窗口判定是否可用。
没有传入 heartbeat 时，`ProcessHealth` 只表示配置级检查结果，适合本地 CLI smoke 和启动前检查。

## 指标

第一版至少记录：

- HTTP 请求数和耗时。
- 任务执行数、耗时和失败数。
- outbox pending/publishing/dead_letter 数量和投递耗时。
- migration preflight/apply 成功失败数。
- tenant isolation guard failure 数。
- rate limit 命中数。
- 数据库连接状态。
- 存储访问失败数。

当前底座已固定第一批低基数字段指标名称：

```text
http_requests_total
http_request_duration_seconds
outbox_events_pending
outbox_events_publishing
outbox_events_dead_letter
outbox_dispatch_events_total
outbox_dispatch_duration_seconds
migration_preflight_total
migration_apply_total
tenant_isolation_guard_failures_total
rate_limit_hits_total
quota_exceeded_total
external_http_requests_total
```

`GET /metrics` 暴露 Prometheus text/plain 响应。底座启动时会创建进程内 `MetricsRegistry`，
HTTP middleware 已记录 `http_requests_total{method,route,status_class}`，rate limit 和 quota 拒绝路径已分别记录
`rate_limit_hits_total{reason,route,rule}`、`quota_exceeded_total{metric,scope}`。Outbox dispatcher 可注入
`MetricsRegistry`，记录 `outbox_dispatch_events_total{outcome}` 并刷新 pending/publishing/dead_letter gauge。
其他 task、migration 指标后续复用同一个 registry 写入，指标名称必须沿用上面的 contract，避免看板和告警反复迁移。

## 设计要求

- 日志必须结构化。
- 敏感字段必须脱敏。
- 每个请求必须有 request_id。
- 私有化部署也要能本地查看日志和健康状态。
- 指标必须带有限标签，避免 tenant_id、user_id 等高基数字段进入 Prometheus labels。
- trace 必须跨 HTTP、task、outbox handler 传播 `trace_id`。
