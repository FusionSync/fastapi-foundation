# Deployment And Process Model

## 目标

部署文档定义本框架在 local、private、cloud 三种 profile 下的运行拓扑、进程角色、健康检查和发布约束。它不是最终 IaC 文件，但后续 Docker Compose、Kubernetes、Helm 和私有化安装包必须遵守这里的进程模型。

## 进程角色

```text
server
  FastAPI HTTP API
  exposes /healthz /readyz /version /metrics

worker
  executes async tasks
  consumes task queue

scheduler
  creates scheduled task triggers
  never runs business logic directly

outbox-dispatcher
  claims outbox_events
  invokes local handlers or external queue adapters

migrate
  runs migration plan/preflight/dry-run/apply/status/drift-check
```

每个角色必须有独立 CLI 命令、配置段、日志字段、健康检查和优雅停机流程。

## Local Profile

```text
server
  -> SQLite or local PostgreSQL
  -> local file storage
  -> sync tasks
```

约束：

- 允许单进程运行。
- `sync` task provider 只用于开发和演示。
- SQLite 不能作为迁移正确性的唯一验证环境。

## Private Profile

```text
Ingress / reverse proxy / TLS
  -> server replicas
  -> PostgreSQL
  -> Redis
  -> MinIO or local object storage
  -> Keycloak or local OIDC
  -> worker replicas
  -> scheduler singleton or locked active instance
  -> outbox-dispatcher replicas
```

约束：

- PostgreSQL 是生产数据源。
- Redis 或等价组件用于 cache、locks、rate limit、coordination。
- scheduler 必须通过 lock 或平台 leader election 避免重复触发。
- outbox-dispatcher 可以多副本，但必须使用 outbox 原子领取协议。

## Cloud Profile

```text
Cloud Load Balancer / WAF / TLS
  -> server autoscaling group
  -> managed PostgreSQL
  -> managed Redis
  -> S3-compatible object storage
  -> Logto/OIDC
  -> worker autoscaling group
  -> scheduler singleton
  -> outbox-dispatcher autoscaling group
  -> metrics/logs/traces backend
```

约束：

- 默认启用标准 HTTP status，不启用 always-200 兼容模式。
- 生产 JWT/local auth 只能用于 break-glass 或显式配置，不作为默认公网认证。
- 建议启用 repository guard + PostgreSQL RLS 兜底。

## Health Checks

```text
server /healthz
  process alive

server /readyz
  database, cache, storage, auth metadata ready

worker health
  queue reachable, database reachable, worker heartbeat fresh

scheduler health
  lock/leader state valid, last trigger heartbeat fresh

outbox-dispatcher health
  database reachable, claim loop heartbeat fresh

migrate health
  one-shot command exit code and JSON output
```

## Shutdown

- server 停止接收新请求，等待 in-flight request 完成或超时。
- worker 停止领取新任务，当前任务按 provider 能力 drain 或安全中断。
- scheduler 释放 leader/lock，不再创建新 trigger。
- outbox-dispatcher 停止领取新事件，正在处理的事件完成后标记；超时未完成依赖 `locked_until` 恢复。

## Release Order

```text
1. config check
2. migration plan
3. migration preflight
4. backup readiness check
5. apply expand migration
6. deploy compatible code
7. run smoke checks
8. run backfill or async repair
9. apply contract migration when safe
```

破坏性 schema 变更必须走 expand-contract，除非作为维护窗口的一次性变更被明确批准。
