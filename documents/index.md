# FastAPI Foundation 入门主页

这是新手的第一入口：从“最短可运行路径”开始，后续再深入到模块开发和运维。

## 5 分钟上手图（建议顺序）

1. 启动前环境确认（`core --help`）
2. 生成配置并校验 `core config template --profile local --json`
3. 校验配置 `core check-config --profile local --json`
4. 生成演示 APP `core bootstrap-app books --target-root src --package platform_apps --json`
5. 检查模块 `core check-app platform_apps.books.module --json`
6. 预检启动 `core serve --dry-run --json`
7. 正式启动 `core serve --run --host 127.0.0.1 --port 8000 --json`
8. 用 `core` 命令和 API 做一次登录 + 业务接口验证

## 你最先看的文档（按“先跑起来”优先）

- [快速上手（可直接执行）](quickstart.md)
- [新手开发手册（从零到一个模块）](guides/new-developer-workbook.md)
- [APP 标准开发方式](guides/app-development.md)
- [身份认证与租户上下文](guides/authentication.md)
- [授权与角色](guides/authorization-and-roles.md)
- [列表查询与表关系](guides/data-relationships.md)
- [CLI 说明与运维命令](guides/cli-guide.md)
- [发布前检查清单](guides/quickstart-checklist.md)

## 系统默认入口（本地启动后快速验证）

- `GET /healthz`：服务可达性
- `GET /readyz`：依赖与初始化状态
- `GET /version`：版本与启动元信息
- `GET /docs`：OpenAPI
- `GET /metrics`：Prometheus 指标

## 关键业务端点（示例）

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/me`
- `POST /api/v1/platform/tenants`
- `GET /api/v1/platform/tenants/{tenant_id}/members`

## 你可以直接照着这 4 条线走完一个完整闭环

1. **应用开发线**：`quickstart` → `new-developer-workbook` → `app-development`  
2. **安全线**：`authentication` → `authorization-and-roles`  
3. **数据线**：`data-relationships` → `api/tenant-isolation`  
4. **发布线**：`cli-guide` → `quickstart-checklist` → `troubleshooting`
