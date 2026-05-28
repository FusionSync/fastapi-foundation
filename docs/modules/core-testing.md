# Core Testing

## Progress

- Status: `partial`
- Done: contract/integration 测试目录、app conformance gate、tenant isolation、security、outbox、migration、permission facts/projection、task/scheduler、CLI error envelope、config profile/drift、app lifecycle、platform app foundation 等 checkpoint 测试已落地。
- Next:
  - [ ] 按 Foundation Roadmap 拆分大功能 checkpoint suites。
  - [ ] 增加业务 app fixture、tenant/user fixture 和发布前完整验证清单。

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
- 每个注册 app 必须通过 app conformance test。
- API contract test 必须校验 HTTP status、业务 code、headers、envelope schema 和 `request_id`。
- serialization golden test 已覆盖 datetime、Decimal、UUID、Enum、空值和列表响应；新增编码规则时必须先扩展 golden test。
- 兼容模式 `always_200` 必须单独测试，不能影响默认生产模式。
- CLI contract test 必须覆盖成功输出、参数错误 exit code `2`、显式确认缺失、运行期异常和 JSON error envelope，保证发布脚本只依赖 stdout 与进程退出码。
- Config profile contract test 必须覆盖模板输出、生产 secret reference、drift-check 成功/失败路径和敏感值脱敏。
- App runtime contract test 必须覆盖 lifecycle startup/shutdown 执行顺序、handler 签名 conformance 和 startup 失败策略。

## 最小测试矩阵

每个业务 app 至少覆盖：

- 成功创建。
- 列表分页。
- 租户隔离。
- 权限拒绝。
- 参数校验错误。
- service 异常到 code 的映射。

## App Conformance Gate

框架必须提供可复用检查：

```text
core check-app
pytest tests/contract/test_app_conformance.py
```

检查项：

- `module.py` 字段完整且类型正确。
- 标准文件存在：`schemas.py`、`models.py`、`router.py`、`services.py`。
- router 使用 core router 工厂。
- schema 继承 core schema 基类。
- tenant-scoped model 只能通过 tenant-safe repository/query 访问。
- app 权限、事件、任务、调度定义可被 registry 收集。
