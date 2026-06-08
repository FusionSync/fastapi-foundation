# Core CLI

## Progress

- Status: `connected`
- Done: `bootstrap-app`、`check-app`、`list-apps`、config template/drift-check/artifacts/write-artifacts、release checkpoint、dependency-probes、permissions、migrate plan/preflight/dry-run/apply/status/drift-check/run、显式 Alembic apply、migration phase 参数和 execution records、serve run dry-run、outbox dispatch/dead-letter、outbox-dispatcher run/profile 参数、scheduler run-once/run/profile 参数、worker run-once/run、worker task queue provider/profile 参数、tasks、idempotency expire/diagnose、MQ check/publish-json/consume-one、operations/smoke、统一 JSON error envelope 和 exit code 契约已接入。
- Next: _none_

## 职责

CLI 模块提供后台框架的命令行入口，避免初始化、运维和诊断脚本散落。

## 目录建议

```text
src/core/cli/
  main.py
  commands/
    apps.py
    config.py
    db.py
    users.py
    permissions.py
```

## 必备命令

```text
check-config
config template
config drift-check
config write-artifacts
bootstrap-app
list-apps
init-db
migrate
serve
serve --run
worker
scheduler
outbox-dispatcher
smoke
backup-check
create-superuser
seed-permissions
show-routes
health-check
outbox dispatch-once
outbox-dispatcher --run
outbox dead-letter list
outbox dead-letter replay
tasks failed list
tasks failed retry
idempotency expire
idempotency diagnose
migrate run
mq check
mq publish-json
mq consume-one
```

`migrate apply` 必须传 `--yes`，并在执行前复用 migration preflight gate。破坏性迁移还必须传 `--backup-ready`。未传 `--alembic-config` 时只返回 `mode=metadata-apply-disabled`、`applied=false`，避免 CI/CD 把 no-op 当作已应用；显式传 `--alembic-config <path>` 时使用 Alembic executor 执行 manifest 绑定 revision，并可用 `--database-url` 覆盖配置中的连接。
`migrate run` 是 migrate 进程角色入口；默认按 `plan -> preflight -> dry-run` 输出发布流水线 envelope，传 `--apply --yes` 后复用 `migrate apply` 门禁和 executor 路径。
`serve --run --dry-run` 加载 `create_app()` 和已安装 app，输出 host、port、route_count 与 server `ProcessHealth`；不传 `--dry-run` 时使用同一配置启动 Uvicorn。
`config template --profile <profile>` 输出 profile 环境变量、五类进程启动命令和发布验证命令，是后续 Docker Compose/Helm/systemd 模板的单一来源。
`config drift-check --profile <profile>` 对比实际环境变量与 profile 模板，缺失或不匹配时返回非零 exit code 和脱敏漂移报告；发布脚本可用重复 `--actual KEY=VALUE` 传入待验证配置，也可传 `--role <role>` 校验具体进程角色的 `OBSERVABILITY__SERVICE_ROLE`。
`config artifacts --profile <profile> --target <docker-compose|systemd|helm-values>` 从 profile 模板生成部署产物内容；重复传入 `--actual KEY=VALUE` 时会附带执行配置 drift-check，并用非零 exit code 阻断漂移产物；配合 `--role` 可校验单个进程角色环境。
`config write-artifacts --profile <profile> --target <docker-compose|systemd|helm-values> --output-dir <dir>` 把部署产物实际写到目录；Docker Compose 目标包含 `Dockerfile`，Helm 目标包含 chart、values 和 workload template。
`bootstrap-app <app_name> --target-root src` 生成后端业务 app 骨架，默认写入 `src/apps/<app_name>`，包含 `module.py`、模型、schema、router、service、权限、迁移 manifest 和 contract test；目标目录已存在时返回非零 exit code，避免覆盖业务代码。
`release checkpoint --profile <profile> --artifact-target <target>` 是发布脚本入口，会串联 profile template、部署产物、config check、backup readiness、按角色 config drift、dependency-probes、migrate dry-run 和 smoke，并输出五个进程角色的参数矩阵。
`dependency-probes` 默认做 profile 依赖配置门禁，确认生产 profile 使用非 `sync` 任务队列，并声明 Redis、对象存储和 OIDC 目标；传 `--probe-dependencies` 时会对 Redis TCP、配置了的 RabbitMQ TCP 和 HTTP 依赖执行真实探活，用于候选环境或发布后 smoke。
`mq check|publish-json|consume-one` 提供 RabbitMQ 基础验证入口；`--url` 可覆盖 `DEPENDENCIES__RABBITMQ_URL`，JSON 输出会脱敏 URL password。`publish-json` 默认使用 queue 作为 routing key 并声明 durable queue，`consume-one` 取到消息后自动 ack。
`migrate plan|preflight|dry-run|apply|run --phase <phase>` 可把迁移计划限制到 expand/backfill/contract/maintenance 单阶段；apply/dry-run 输出 `execution_records`，记录每条目标 migration 的 rollback strategy 和 forward-fix 要求。
`outbox dispatch-once` 必须通过 `--installed-app` 或 settings 加载 AppModule 后构建 `EventRegistry`，领取 pending/failed outbox event，投递到已注册 handler，并输出 claimed/published/failed/dead_lettered JSON。
`outbox-dispatcher --run` 是运行角色入口；不传 `--max-iterations` 时持续循环，传入后用于本地 smoke/CI 做有限轮验证；传 `--instance-id` 时写入进程 heartbeat。CLI 会响应 SIGTERM/SIGINT，在当前轮处理完成后退出；profile 模板通过 `OUTBOX_DISPATCHER__BATCH_SIZE` 和 `OUTBOX_DISPATCHER__IDLE_SLEEP_SECONDS` 参数化批量领取和空闲休眠。
`scheduler --run-once` 通过 `--installed-app` 加载 schedule/task handler，触发指定 `--schedule-id`，写入 `TaskRun` 和 `ScheduleTriggerLog`，用于 local profile、运维手动触发和 CI smoke。
`scheduler --run` 扫描 AppModule cron schedule definition，按 tenant、当前分钟和持久 `ScheduleState` 触发 due schedule；`--max-iterations` 用于 local/CI 有限轮验证，不传则常驻轮询；`--idle-sleep-seconds` 和 `--lock-ttl-seconds` 可由 profile 模板参数化，传 `--instance-id` 时写入进程 heartbeat。
`worker --run-once` 通过 `--installed-app` 加载 task handler，按 `--queue` 和 `--provider sync|database` 领取一个 pending `TaskRun` 并执行，输出 claimed 和 task_result；它是 local/CI 有限轮验证入口，不是后台常驻 worker loop。`--provider celery` 用于配置识别，但实际执行必须由 Celery worker 消费 `core.tasks.execute`，不会由 core worker 静默执行。
`worker --run` 使用同一执行契约循环领取 pending `TaskRun`；`--max-iterations` 用于 CI/运维有限轮验证，不传则持续运行，空队列时按 `--idle-sleep-seconds` 休眠；`--max-attempts` 和 `--retry-backoff-seconds` 控制 database queue 自动重试，传 `--instance-id` 时写入进程 heartbeat。
`tasks failed retry` 必须传 `--yes`，并通过 `--installed-app` 或 settings 加载 AppModule 后执行已注册任务处理器。
`idempotency expire` 必须传 `--yes`，把超过 `expires_at` 的幂等记录标记为 `expired`；`idempotency diagnose` 按 tenant/user/route/key/request_hash 输出 replay、冲突、处理中、失败重试和过期可复用诊断。
`smoke --profile <profile> --json` 必须输出 config 检查和所有运行角色的 `role_health` 明细，便于 CI/CD 在发布后判断 server、worker、scheduler、outbox-dispatcher、migrate 是否满足当前 profile 的运行门禁。

失败命令在 `--json` 模式下必须输出稳定 envelope，便于发布脚本只解析 stdout：

```json
{
  "ok": false,
  "command": "tasks failed retry",
  "exit_code": 1,
  "error": {
    "code": "CLI_CONFIRMATION_REQUIRED",
    "message": "tasks failed retry requires --yes",
    "details": {}
  }
}
```

参数解析错误返回 exit code `2` 和 `CLI_USAGE_ERROR`；缺少 `--yes` 等显式确认返回 exit code `1` 和 `CLI_CONFIRMATION_REQUIRED`；运行期异常返回 exit code `1` 和 `CLI_RUNTIME_ERROR`，`error.details.exception_type` 保留异常类型。

## 设计要求

- CLI 与 server 共享同一套 settings 加载逻辑。
- CLI 命令必须能在私有化环境中离线执行。
- 危险命令需要确认参数，例如 `--yes`。
- 命令输出应适合 CI/CD 和人工阅读。
