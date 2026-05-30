# Quickstart：第一次上手（可直接执行）

目标：让你在本地 20 分钟内从“零”到“服务可访问”。  
每一步都带有**参数说明 + 验证口径**，可直接复制执行。

## 1）前置准备

```bash
# 通用
python -m venv .venv
python -m pip install -U pip
python -m pip install -e .
core --help
```

> 若命令不可用，可改用 `python -m core.cli.main --help`。

## 2）创建最小 `.env`（先用本地 profile）

```bash
APP__ENV=local
APP__NAME="FastAPI Foundation Demo"
DATABASE__URL=sqlite+aiosqlite:///./data/local.db
SECURITY__JWT_SECRET=change-me-only-local
INSTALLED_APPS=["platform_apps.platform_accounts","platform_apps.platform_tenants","platform_apps.notes"]
```

你也可以先看官方模板并按参数生成：

```bash
core config template --profile local --json
```

### 配置参数含义（最短清单）

| 参数 | 说明 |
|---|---|
| `APP__ENV` | 当前环境：`local` / `private` / `cloud` |
| `DATABASE__URL` | SQLAlchemy 异步数据库连接串 |
| `SECURITY__JWT_SECRET` | JWT 签名密钥；本地演示可短字符串 |
| `INSTALLED_APPS` | 启动时加载的 APP 模块路径列表 |

## 3）快速验证配置是否健康（必跑）

```bash
core config drift-check --profile local --json
core check-config --profile local --json
core list-apps --json
core config template --profile local --json
```

判定规则：

- `core config drift-check` 的 `ok` 为 `true`。
- `core check-config` 的 `ok` 为 `true`。
- `core list-apps` 返回的 `apps` 不为空且每项 `permissions/routers/version` 合法。

## 4）先出一个可验证的 APP 脚手架

```bash
core bootstrap-app notes --target-root src --package platform_apps --json
```

预期输出字段：

- `ok: true`
- `module_path: platform_apps.notes.module`
- `files` 包含 `models.py` `schemas.py` `services.py` `repositories.py` `router.py` `permissions.py` `module.py`

## 5）检查模块一致性

```bash
core check-app platform_apps.notes.module --json
```

`check-app` 通过条件：

- `ok: true`
- `errors` 为空数组
- `diagnostics` 不出现阻塞类问题

## 6）启动预检（不会启动服务）

```bash
core serve --dry-run --host 127.0.0.1 --port 8000 --json
core migrate plan --json
```

`--dry-run` 的核心校验是：

- 组件加载与 `INSTALLED_APPS` 解析
- 路由安全策略是否和 `PermissionSpec` 对齐
- 数据库引擎可初始化（但不执行真实服务）

## 7）启动服务并做健康检查

```bash
core serve --run --host 127.0.0.1 --port 8000 --json
```

另开一个终端验证：

```bash
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/readyz
curl -s http://127.0.0.1:8000/version
curl -s http://127.0.0.1:8000/docs | head -n 1
```

## 8）首次登录与 me 接口（无前端）

> 以下接口需要数据库里已有用户；如果是空库，先从运维/种子数据补齐用户和租户。

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"change-me","tenant_id":"tenant_demo"}' \
  | jq -r '.data.access_token')

curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/me | jq .
```

若返回：
- `AUTH_INVALID_TOKEN`：token 过期/签名错误/未登录。
- `TENANT_ACCESS_DENIED`：登录时未带 `tenant_id` 或请求上下文缺少 tenant。

## 9）用最小命令链形成“上手闭环”（建议放到脚本中）

```bash
core config drift-check --profile local --json
core check-config --profile local --json
core check-app --all --json
core permissions catalog --installed-app platform_apps.platform_tenants.module --json
core permissions reconcile --installed-app platform_apps.platform_tenants.module --database-url "$DATABASE__URL" --repair --json
core migrate plan --json
core serve --dry-run --host 127.0.0.1 --port 8000 --json
core smoke --profile local --json
```

### 重要说明

- `core permissions reconcile` 是角色投影命令：本地验证可无 `--repair` 查看元数据，生产环境建议加 `--repair`。
- `core serve --run` 是正式运行；`core serve --dry-run` 是安全预检。
- 以上命令可以作为 CI/CD 和本地 `Makefile`/`Taskfile` 的基线。
