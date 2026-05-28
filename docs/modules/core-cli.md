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
create-superuser
seed-permissions
show-routes
health-check
outbox dead-letter list
outbox dead-letter replay
```

## 设计要求

- CLI 与 server 共享同一套 settings 加载逻辑。
- CLI 命令必须能在私有化环境中离线执行。
- 危险命令需要确认参数，例如 `--yes`。
- 命令输出应适合 CI/CD 和人工阅读。
