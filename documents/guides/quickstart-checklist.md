# Quickstart Checklist（上线前必跑）

将下面命令按顺序执行，并在每步确认返回值。

## A. 环境与配置

- [ ] `.env` 已包含：
  - `APP__ENV`
  - `APP__NAME`
  - `DATABASE__URL`
  - `SECURITY__JWT_SECRET`
  - `INSTALLED_APPS`
- [ ] `core config template --profile local --json` 与本地 `DATABASE__URL`、`APP__NAME` 一致
- [ ] `core config drift-check --profile local --json` 无关键漂移
- [ ] `core check-config --profile local --json` 返回 `ok=true`

## B. 模块与路由

- [ ] `core bootstrap-app <label> --target-root src --package platform_apps --json` 成功
- [ ] `core check-app <module>.module --json` 返回 `ok=true`
- [ ] `core check-app --all` 无阻塞错误
- [ ] `core list-apps --json` 显示模块名、版本、权限、路由数

## C. 权限模型

- [ ] `PermissionSpec` 中 `resource:action` 与路由权限字符串一致
- [ ] `route_authorization_decision` 已用于写接口
- [ ] `core permissions catalog --installed-app ... --json` 返回预期权限
- [ ] `core permissions reconcile --installed-app ... --database-url "$DATABASE__URL" --repair --json` 通过

## D. 运行与健康

- [ ] `core migrate plan --json` 能生成计划
- [ ] `core serve --dry-run --json` 成功
- [ ] `core serve --run --dry-run --json` 可再次验证不改动状态
- [ ] 运行服务后：
  - [ ] `/healthz` 可达
  - [ ] `/readyz` 可达
  - [ ] `/version` 有版本元数据

## E. 认证/租户链路

- [ ] 可成功登录：`POST /api/v1/auth/login`（拿到 token）
- [ ] `GET /api/v1/me` 使用 token 成功
- [ ] `TENANT_CONTEXT_CONFLICT` / `TENANT_ACCESS_DENIED` 已按预期理解
- [ ] 有权限用户可访问受保护业务路由

## F. 迁移与任务（发布前可选）

- [ ] `core smoke --profile local --json`（或目标环境 `--profile`）通过
- [ ] `core backup-check --profile local --json` 检查通过或给出修复路径
- [ ] 出现任务退避/死信时运行：
  - `core outbox dead-letter list --json`
  - `core tasks failed list --json`
  - `core idempotency diagnose --tenant-id ... --user-id ... --route ... --idempotency-key ... --json`
