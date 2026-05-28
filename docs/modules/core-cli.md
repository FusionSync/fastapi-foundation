# Core CLI

## Progress

- Status: `partial`
- Done: `check-app`、`list-apps`、permissions、migrate plan/preflight/dry-run/apply/status/drift-check、显式 Alembic apply、outbox dispatch/dead-letter、outbox-dispatcher run、scheduler run-once、worker run-once、tasks、operations/smoke 等命令骨架已接入。
- Next:
  - [ ] 补 server/migrate 角色启动命令和 worker/scheduler 后台 loop。
  - [ ] 统一 CLI exit code、JSON error envelope 和发布脚本契约。

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
list-apps
init-db
migrate
serve
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
```

`migrate apply` 必须传 `--yes`，并在执行前复用 migration preflight gate。破坏性迁移还必须传 `--backup-ready`。未传 `--alembic-config` 时只返回 `mode=metadata-apply-disabled`、`applied=false`，避免 CI/CD 把 no-op 当作已应用；显式传 `--alembic-config <path>` 时使用 Alembic executor 执行 manifest 绑定 revision，并可用 `--database-url` 覆盖配置中的连接。
`outbox dispatch-once` 必须通过 `--installed-app` 或 settings 加载 AppModule 后构建 `EventRegistry`，领取 pending/failed outbox event，投递到已注册 handler，并输出 claimed/published/failed/dead_lettered JSON。
`outbox-dispatcher --run` 是运行角色入口；不传 `--max-iterations` 时持续循环，传入后用于本地 smoke/CI 做有限轮验证。
`scheduler --run-once` 通过 `--installed-app` 加载 schedule/task handler，触发指定 `--schedule-id`，写入 `TaskRun` 和 `ScheduleTriggerLog`，用于 local profile、运维手动触发和 CI smoke。
`worker --run-once` 通过 `--installed-app` 加载 task handler，按 `--queue` 领取一个 pending `TaskRun` 并执行，输出 claimed 和 task_result；它是 local/CI 有限轮验证入口，不是后台常驻 worker loop。
`tasks failed retry` 必须传 `--yes`，并通过 `--installed-app` 或 settings 加载 AppModule 后执行已注册任务处理器。
`smoke --profile <profile> --json` 必须输出 config 检查和所有运行角色的 `role_health` 明细，便于 CI/CD 在发布后判断 server、worker、scheduler、outbox-dispatcher、migrate 是否满足当前 profile 的运行门禁。

## 设计要求

- CLI 与 server 共享同一套 settings 加载逻辑。
- CLI 命令必须能在私有化环境中离线执行。
- 危险命令需要确认参数，例如 `--yes`。
- 命令输出应适合 CI/CD 和人工阅读。
