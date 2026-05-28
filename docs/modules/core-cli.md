# Core CLI

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
outbox dead-letter list
outbox dead-letter replay
tasks failed list
tasks failed retry
```

`migrate apply` 当前是 metadata mode，必须传 `--yes`，并在执行前复用 migration preflight gate。破坏性迁移还必须传 `--backup-ready`。
`tasks failed retry` 必须传 `--yes`，并通过 `--installed-app` 或 settings 加载 AppModule 后执行已注册任务处理器。
`smoke --profile <profile> --json` 必须输出 config 检查和所有运行角色的 `role_health` 明细，便于 CI/CD 在发布后判断 server、worker、scheduler、outbox-dispatcher、migrate 是否满足当前 profile 的运行门禁。

## 设计要求

- CLI 与 server 共享同一套 settings 加载逻辑。
- CLI 命令必须能在私有化环境中离线执行。
- 危险命令需要确认参数，例如 `--yes`。
- 命令输出应适合 CI/CD 和人工阅读。
