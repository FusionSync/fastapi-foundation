# Core Testing

## Progress

- Status: `connected`
- Done: contract/integration 测试目录、app conformance gate、错误码和 message catalog metadata/app 注册检查、repository 继承检查、route permission conformance 与权限拒绝审计、admin/migration metadata diagnostics、binary response conformance whitelist、app registry version/capability diagnostics、Settings 派生 runtime capability、runtime startup diagnostics、tenant isolation、security、security hardening checklist、rate-limit middleware/sliding-window、observability request logging/monitoring contract、outbox schema/version/error classification/shutdown/profile 参数、migration phase/execution records、database runtime diagnostics/read split/tenant fallback、database/Redis lock provider、Redis cache provider、cache invalidation rules、database quota usage store、file upload quota gate、quota mutation/task submit gate、S3 storage provider、file virus scan/retention cleanup、file resource authorization adapter、tenant lifecycle configurable policy diagnostics、idempotency replay/diagnostics CLI、idempotency mutation guard accounts/files/tasks checkpoint、account security events/token refresh/failed login audit、platform accounts HTTP API profile/password/identity/session checkpoint、external OIDC provider callback checkpoint、task database queue provider/retry backoff/profile 参数、scheduler persistent state/misfire/profile 参数、tenant invitation/RoleGrant projection、platform tenants HTTP API service wiring、tenant deletion orchestration retry checkpoint、outbox/migration/audit hash chain 跨进程锁使用点、audit WORM/SIEM export、permission facts/projection、cross-tenant platform gate、route authorization dependency、task/scheduler trace handoff、CLI error envelope、config profile/drift/deployment artifacts、release checkpoint suite、dependency-probes、app lifecycle diagnostics、API list query contract、platform app foundation、业务 app fixture、tenant/user fixture 和发布前完整验证清单等 checkpoint 测试已落地。
- Next: _none_

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

当前落地入口集中在 `core.testing`：

```python
from core.testing import (
    build_prerelease_checklist,
    create_business_app_fixture,
    create_tenant_user_fixture,
)
```

- `create_business_app_fixture(label, target_root=...)` 默认生成 `test_apps.{label}` 后端业务 app 测试骨架，返回 `module_path`、`Settings(installed_apps=[...])`、生成文件列表和 `check_app` 命令。
- `create_tenant_user_fixture(...)` 返回可直接用于 service/router 测试的 `Tenant`、`TenantMember`、auth `CurrentUser`、tenancy resolver `CurrentUser` 和 `RequestContext`。
- `build_prerelease_checklist(profile, artifact_target, installed_apps)` 输出发布前必须执行的 lint、pytest、diff、app conformance、permission catalog、migration plan、release checkpoint 和生产 profile dependency probe 命令。

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
- App registry contract test 必须覆盖 dependency-first 排序、core version gate、Settings 派生 capability gate、`list-apps` diagnostics 和 `/readyz` diagnostics。
- Config profile contract test 必须覆盖模板输出、生产 secret reference、private/cloud security hardening 清单、按进程角色执行的 drift-check 成功/失败路径、部署产物渲染和敏感值脱敏。
- Config profile contract test 必须覆盖 profile monitoring 契约、drift alert 输出和部署产物中的 alert rules 渲染。
- Security contract test 必须覆盖 local/private/cloud hardening checklist，确保 CSP、cookie、TLS/HSTS 和 header 控制项不会从生产 profile 中漂移。
- Release checkpoint contract test 必须覆盖 profile 参数矩阵、部署产物、按角色 drift gate、backup readiness、migrate dry-run 和 smoke 聚合输出。
- App runtime contract test 必须覆盖 lifecycle startup/shutdown 执行顺序、handler 签名 conformance 和 startup 失败策略。
- App runtime readiness contract test 必须覆盖 runtime registry counts、provider readiness 合并和 provider 失败时的统一 startup diagnostics。
- Observability contract test 必须覆盖请求结构化日志字段；task/outbox/scheduler integration test 必须覆盖非 HTTP 背景上下文的 `trace_id` handoff。
- App conformance contract test 必须覆盖 `FileResponse`/`StreamingResponse` 例外，以及 `JSONResponse` 不能绕过 typed envelope 的拒绝路径。
- App conformance contract test 必须覆盖 admin metadata dotted path 诊断和 migration manifest metadata 诊断，错误消息要能定位 app、metadata 类型、id/path 和具体字段。
- App conformance contract test 必须覆盖业务错误码 metadata、owner/app label 一致性、跨 app 重复声明和 runtime 注册。
- App conformance contract test 必须覆盖 message catalog owner/code/deprecated gate、registry 注册和 i18n fallback。
- App conformance contract test 必须覆盖 route-level permission 格式，以及 route permission 必须在 `AppModule.permissions` 声明。
- App conformance contract test 必须覆盖 tenant-scoped model repository 继承约束，拒绝裸 `BaseRepository` 访问租户模型。
- Events/outbox contract test 必须覆盖 event schema 注册、版本兼容声明、payload schema 写入校验、dispatcher 投递前 schema 校验，以及 transient/permanent handler 错误分类。
- Idempotency contract/integration test 必须覆盖 insert-and-claim、processing conflict、request hash conflict、response replay、过期 reclaim、过期清理 CLI 和诊断 CLI。
- Lock provider test 必须覆盖 owner token 校验、TTL 过期重领、`fencing_token` 递增和稳定 `LOCK_NOT_ACQUIRED` code；outbox/migration/audit integration test 必须覆盖跨进程锁占用时不会执行受保护动作。

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
- 指向 tenant-scoped model 的 app repository 必须继承 `TenantScopedRepository` 或 `CrossTenantRepository`。
- app 权限、事件、任务、调度定义可被 registry 收集。
- app router 的 route-level permissions 必须使用 `resource:action`，并能在 app 权限目录中找到。
- app 错误码声明 metadata 完整，并会在 `AppRegistry.load()` 时注册到统一 exception registry。
- app message catalog 只能覆盖本 app 已声明且未 deprecated 的错误码，并会在 `AppRegistry.load()` 时注册到统一 message registry。
- admin route/widget/model metadata dotted path 可导入，migration manifest metadata 可解析且通过字段级校验。
