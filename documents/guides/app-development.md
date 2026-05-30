# App 开发指南

## 你只需要做这 5 步

1. 执行脚手架

```bash
core bootstrap-app your_app --target-root src
```

2. 编辑 `src/apps/your_app` 下的标准文件：

- `module.py`
- `schemas.py`
- `models.py`
- `router.py`
- `services.py`
- `permissions.py`
- `repositories.py`（如有数据库访问）

3. 执行约束检查

```bash
core check-app apps.your_app.module --json
```

4. 把模块加入 `installed_apps`

5. 启动并验证基础 endpoint

```bash
core check-config --profile local --json
core serve --json
```

## 关键约束

- 路由请使用 `core.base.create_router`。
- 响应请使用统一 Envelope。
- 多租户模型请继承 `TenantScopedModel`。
- 租户相关查询请通过 `TenantScopedRepository`。
