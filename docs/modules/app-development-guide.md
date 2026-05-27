# App Development Guide

## 新增业务 app 流程

1. 在 `src/apps/{app_name}` 创建目录。
2. 定义 `models.py`，需要租户隔离的模型继承 `TenantScopedModel`。
3. 定义 `schemas.py`，区分 create、update、read schema。
4. 定义 `service.py`，所有业务逻辑放在 service 层。
5. 定义 `router.py`，只处理依赖、入参和响应。
6. 在 `permissions.py` 声明权限点。
7. 在 `module.py` 组装 `AppModule`。
8. 把 module path 加入 `INSTALLED_APPS`。
9. 生成迁移并补充测试。

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
- 事件发布在事务成功后执行。
- 复杂跨表更新必须有测试覆盖。

## 测试要求

每个 app 至少覆盖：

- router 权限检查。
- service 核心业务规则。
- repository 查询过滤。
- 租户隔离。
- 失败分支和异常转换。
