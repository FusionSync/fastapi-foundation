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
- scheduler 必须通过 `LockedScheduleProvider` 注入分布式 lock，或使用平台 leader election，避免重复触发同一 planned slot。
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
  config loaded, database configured, database reachable, AppRegistry loaded, MetricsRegistry loaded
  returns HTTP 503 when not ready

worker health
  queue reachable, database reachable, worker heartbeat fresh

scheduler health
  lock/leader state valid, last trigger heartbeat fresh

outbox-dispatcher health
  database reachable, claim loop heartbeat fresh

migrate health
  one-shot command exit code and JSON output
```

`core smoke --profile <profile> --json` 必须聚合 `server`、`worker`、`scheduler`、
`outbox-dispatcher`、`migrate` 的 `ProcessHealth`，输出每个角色的 checks 和 details。
local profile 可以使用 sync task provider；private/cloud profile 后续接 Redis、队列和 leader lock 时必须复用同一输出结构。
`core config template --profile <profile> --json` 是部署 profile 的配置和进程矩阵来源；发布脚本、Docker Compose、Helm 或 systemd 示例必须从其中的 `env`、`processes` 和 `validation_commands` 派生，避免文档、脚本和 CLI 参数漂移。
`core config artifacts --profile <profile> --target <docker-compose|systemd|helm-values> --json` 会直接从该矩阵输出部署产物内容；发布仓库或安装包应消费 `files` 字段，而不是手工复制命令。
发布前应执行 `core config drift-check --profile <profile> --json`，或用重复 `--actual KEY=VALUE` 检查候选部署环境；校验 worker、scheduler、outbox-dispatcher 等角色时传 `--role <role>`，避免把所有运行时都按 server 日志字段检查。失败报告必须脱敏输出缺失/不匹配项，不能泄漏数据库密码或 secret。
生成部署产物时也可以给 `core config artifacts` 传入重复 `--actual KEY=VALUE` 和 `--role <role>`，命令会附带输出 `drift` 并在漂移时返回非零 exit code，适合作为发布参数矩阵的早期 gate。

当前 `core worker` 命令提供配置级健康检查，不强制依赖数据库连接；`core serve --run --dry-run`、`core migrate run`、`core worker --run --max-iterations <n>`、`core scheduler --run-once`、`core scheduler --run --max-iterations <n>` 和 `core outbox-dispatcher --run` 可在 local/CI 中执行启动计划或有限轮运行验证。`worker --run`、`scheduler --run` 和 `outbox-dispatcher --run` 传入 `--instance-id` 时会写入 `process_heartbeats`。
发布脚本调用 `--json` 命令时必须以进程退出码为准，并在失败时读取 stdout 中的 `ok=false`、`exit_code` 和 `error.code/message/details`；参数错误固定为 exit code `2`，运行期失败和显式确认缺失固定为非零 exit code `1`。
生产部署应由对应进程定期写入 `process_heartbeats`，再把最新 `ProcessHeartbeatSnapshot` 传入
`check_process_health()`，使 `heartbeat_status_healthy`、`heartbeat_role_matches` 和
`heartbeat_fresh` 进入统一 `ProcessHealth`。默认 freshness 窗口为 120 秒，部署平台可以按任务类型调整。

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
5. apply expand migration with explicit Alembic config
6. deploy compatible code
7. run smoke checks
8. run backfill or async repair
9. apply contract migration with explicit approval/backup readiness when safe
```

破坏性 schema 变更必须走 expand-contract，除非作为维护窗口的一次性变更被明确批准。
