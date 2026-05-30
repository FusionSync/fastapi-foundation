# Troubleshooting（第一天排障）

## 1）`core` 命令找不到

检查：
- 是否在当前虚拟环境：`python -m pip show -q fastapi-foundation`
- 是否能执行：`core --help`
- `PATH` 是否可见

处理：
- 安装方式改为：`python -m pip install -e .`
- 或者直接 `python -m core.cli.main --help`

## 2）`core check-app` 报错

常见原因：
- `module` 路径不存在
- `module.py` 未导出 `module`
- `PermissionSpec` 与 `router` 路由权限不一致
- 模板路由未包含约定的仓储/响应包裹

建议修复顺序：
1. `core list-apps --json` 先确认加载路径是否正确
2. 打开模块文件逐项比对：`router`、`permissions`、`migrations`
3. 再跑 `core check-app <module> --json`

## 3）`TENANT_ACCESS_DENIED`

出现场景：
- 该路由是 tenant 路由（`tenant_required=True`）
- 请求未带 `X-Tenant-ID` 且 token 没有 `tid`
- 路由用了错误的 `tenant_operation`

处理：
- 先看路由定义是否应为 tenant 或 platform
- 先确认 `AUTH` 和 tenant 解析是否正确
- 调用时补齐 `Authorization` 或 `X-Tenant-ID`

## 4）`TENANT_CONTEXT_CONFLICT`

原因为 token 与 header 租户不一致：

```bash
curl -H "Authorization: Bearer <token-with-tenant-a>" \
  -H "X-Tenant-ID: tenant-b" \
  ...
```

处理：
- 只保留一个来源，优先校验业务设计后固定策略

## 5）登录后马上 `PERMISSION_DENIED`

一般是：
- 路由权限未在 `PermissionSpec` 中定义
- 使用 `platform` scope 路由却授予了 `tenant` 级别权限
- 角色投影未同步

修复顺序：

```bash
core permissions catalog --installed-app platform_apps.platform_tenants.module --json
core permissions reconcile --installed-app platform_apps.platform_tenants.module --database-url "$DATABASE__URL" --repair --json
```

## 6）`migrate apply`/`idempotency expire` 要求确认

这类命令默认带破坏性，必须加确认参数：

- `core migrate apply ... --yes`
- `core idempotency expire ... --yes`
- `core tasks failed retry ... --yes`
- `core tasks running recover ... --yes`
- `core outbox dead-letter replay ... --yes`

如果没加 `--yes`，会返回 `CLI_CONFIRMATION_REQUIRED`。

## 7）服务启动后立刻退出

先检查 `core serve --dry-run --json` 和 `core list-apps --json`。

重点看：
- `migrations` 是否加载
- `installed_apps` 是否包含不存在模块
- 是否有 `ImportError`（缺少 module path 或依赖）

## 8）路由返回 500 但日志没写出来

先确认：
- 是否在 `--json` 下直接运行 CLI，错误码里是否有 `error.code`
- API 运行时是否从 `/readyz` 先确认，避免直接打业务接口
- 关键日志是否启用 request-id：调用头里带 `X-Request-ID` 可便于链路排查

## 9）快速自检建议（每天首次定位问题）

```bash
core config template --profile local --json
core check-config --profile local --json
core list-apps --json
core check-app --all --json
core permissions catalog --json
core serve --run --dry-run --json
```
