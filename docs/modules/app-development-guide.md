# App Development Guide

## Progress

- Status: `connected`
- Done: 标准 app 目录、module 注册、分层边界、事务约束、测试要求、可复制后端业务 app bootstrap 模板和按大功能 checkpoint 推进流程已接入。
- Next: _none_

## 新增业务 app 流程

1. 运行 `core bootstrap-app {app_name} --target-root src` 生成后端业务 app 骨架。
2. 运行 `core check-app apps.{app_name}.module --json`，确认初始骨架通过 conformance。
3. 定义 `models.py`，需要租户隔离的模型继承 `TenantScopedModel`。
4. 定义 `schemas.py`，区分 create、update、read/list schema。
5. 定义 `services.py`，所有业务逻辑放在 service 层。
6. 定义 `router.py`，只处理依赖、入参和响应封装。
7. 在 `permissions.py` 声明权限点。
8. 在 `module.py` 组装 `AppModule`。
9. 把 module path 加入 `INSTALLED_APPS`。
10. 生成迁移并补充测试。

## Bootstrap 模板

`core bootstrap-app` 生成的是后端业务 app 模板，不是前端模板。默认输出到 `src/apps/{app_name}`，并生成：

```text
src/apps/{app_name}/
  __init__.py
  module.py
  schemas.py
  models.py
  router.py
  services.py
  permissions.py
  public_api.py
  events.py
  tasks.py
  migrations/
    __init__.py
    manifest.py
  tests/
    test_{app_name}_contract.py
```

模板默认使用 SQLAlchemy 2.x async 项目的模型基类、core response envelope、`create_router`、`AppModule` 和 `MigrationSpec`，生成后应能直接通过：

```bash
core check-app apps.{app_name}.module --json
```

如果目标目录已存在，`bootstrap-app` 会失败，不会覆盖已有业务代码。

## Checkpoint 推进

业务 app 按大功能 checkpoint 推进，不按零散文件反复修改：

1. 在模块文档或任务说明中写清本 checkpoint 的用户流程、数据模型、权限、事件、任务和验收命令。
2. 先补最小 contract/integration test，确认缺口可复现。
3. 一次性连通 router、service、repository/model、权限、事件/outbox、任务或调度等必要链路。
4. 完成该大功能后更新模块 Progress 的 Done/Next。
5. 集中运行该 checkpoint 的测试、`check-app` 和相关 CLI smoke，再提交。

## 标准目录

```text
src/apps/{app_name}/
  __init__.py
  module.py
  schemas.py
  models.py
  router.py
  services.py
  permissions.py
  events.py
  tasks.py
  tests/
```

允许在复杂 app 中增加 `repositories.py`、`selectors.py` 或子包，但上述文件名必须保留，便于自动扫描、代码生成和团队协作。

## 分层约束

```text
router
  认证、租户、权限、入参、响应

service
  业务规则、事务、事件发布

repository
  查询封装、复杂过滤、raw SQL

models
  ORM 模型和关系
```

## 事务约束

- 写操作由 service 控制事务。
- 可靠事件必须在业务事务内写入 outbox，事务提交后由 dispatcher 发布。
- post-commit hook 只允许用于非关键、可丢弃的本地回调，不能承载审计、权限投影、任务提交、文件清理等可靠副作用。
- 复杂跨表更新必须有测试覆盖。

## 测试要求

每个 app 至少覆盖：

- router 权限检查。
- service 核心业务规则。
- repository 查询过滤。
- 租户隔离。
- 失败分支和异常转换。

## 基类约束

- ORM 模型继承 core 提供的 model 基类或 mixin。
- Pydantic schema 继承 core 提供的 schema 基类。
- router 使用 core 提供的 router 工厂或 router 基类。默认 router 需要认证和租户上下文；登录、健康检查、公开回调等接口必须显式 `public=True`。
- service 继承 core 提供的 service 基类。
- app 不直接构造响应 envelope，统一调用 core response helpers。
