# App Development Guide

## Progress

- Status: `partial`
- Done: 标准 app 目录、module 注册、分层边界、事务约束和测试要求已写入开发规范。
- Next:
  - [ ] 提供一个可复制的业务 app bootstrap 模板，默认通过 app conformance。
  - [ ] 给业务 app 作者补充按大功能 checkpoint 推进的提交流程。

## 新增业务 app 流程

1. 在 `src/apps/{app_name}` 创建目录。
2. 定义 `models.py`，需要租户隔离的模型继承 `TenantScopedModel`。
3. 定义 `schemas.py`，区分 create、update、read/list schema。
4. 定义 `services.py`，所有业务逻辑放在 service 层。
5. 定义 `router.py`，只处理依赖、入参和响应封装。
6. 在 `permissions.py` 声明权限点。
7. 在 `module.py` 组装 `AppModule`。
8. 把 module path 加入 `INSTALLED_APPS`。
9. 生成迁移并补充测试。

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
