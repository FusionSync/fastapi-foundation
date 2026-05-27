# Core Testing

## 职责

Testing 模块提供测试基础设施，避免每个 app 重复搭建测试 app、测试用户、测试租户和测试数据库。

## 目录建议

```text
src/core/testing/
  app.py
  database.py
  auth.py
  tenancy.py
  permissions.py
  factories.py
```

## 核心能力

- 创建测试 FastAPI app。
- 初始化测试数据库。
- 提供测试 client。
- 提供当前用户和当前租户 fixture。
- 提供权限放行或权限模拟工具。
- 提供 ContextVar 测试上下文。
- 提供基础 model factory。

## 测试原则

- app 测试默认启用租户隔离。
- 权限绕过必须显式声明。
- service 测试可以使用内存 provider。
- API 测试必须断言响应 envelope 和业务 code。

## 最小测试矩阵

每个业务 app 至少覆盖：

- 成功创建。
- 列表分页。
- 租户隔离。
- 权限拒绝。
- 参数校验错误。
- service 异常到 code 的映射。
