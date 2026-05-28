# Core CLI

## Progress

- Status: `partial`
- Done: `check-app`、`list-apps`、permissions、migrate、outbox dispatch/dead-letter、outbox-dispatcher run、tasks、operations/smoke 等命令骨架已接入。
- Next:
  - [ ] 补 server/worker/scheduler/migrate 角色启动命令。
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

`migrate apply` 必须传 `--yes`，并在执行前复用 migration preflight gate。破坏性迁移还必须传 `--backup-ready`。当前尚未接入真实 Alembic executor，因此 preflight 通过后也只返回 `mode=metadata-apply-disabled`、`applied=false`，避免 CI/CD 把 no-op 当作已应用。
`outbox dispatch-once` 必须通过 `--installed-app` 或 settings 加载 AppModule 后构建 `EventRegistry`，领取 pending/failed outbox event，投递到已注册 handler，并输出 claimed/published/failed/dead_lettered JSON。
`outbox-dispatcher --run` 是运行角色入口；不传 `--max-iterations` 时持续循环，传入后用于本地 smoke/CI 做有限轮验证。
`tasks failed retry` 必须传 `--yes`，并通过 `--installed-app` 或 settings 加载 AppModule 后执行已注册任务处理器。
`smoke --profile <profile> --json` 必须输出 config 检查和所有运行角色的 `role_health` 明细，便于 CI/CD 在发布后判断 server、worker、scheduler、outbox-dispatcher、migrate 是否满足当前 profile 的运行门禁。

## 设计要求

- CLI 与 server 共享同一套 settings 加载逻辑。
- CLI 命令必须能在私有化环境中离线执行。
- 危险命令需要确认参数，例如 `--yes`。
- 命令输出应适合 CI/CD 和人工阅读。
