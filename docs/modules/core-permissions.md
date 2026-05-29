# Core Permissions

## Progress

- Status: `connected`
- Done: permission registry、authorization decision、platform/tenant scope 校验、route authorization dependency、route permission conformance、projection cache invalidation、resource owner adapter、outbox-backed role grant events、reconciliation CLI 和审计字段要求已落地。
- Next:
  - [ ] 补跨租户平台权限统一 gate。

## 职责

Permissions 模块负责统一授权接口，第一版底层使用 Casbin。

## 权限模型

使用 RBAC with Domains：

```text
subject = user_id
domain = tenant_id
object = resource
action = action
```

示例：

```text
alice, tenant_a, workspace, write -> allow
alice, tenant_b, workspace, write -> deny
```

## 目录建议

```text
src/core/permissions/
  enforcer.py
  deps.py
  model.conf
  registry.py
```

## 业务调用方式

业务 app 只调用 core 抽象：

```python
decision = await AuthorizationService(session).authorize(
    user_id=current_user.id,
    tenant_id=current_tenant.id,
    resource="workspace",
    action="write",
)
```

禁止业务 app 直接操作 Casbin enforcer。
平台级权限使用 `scope=platform` 的 RoleGrant，不允许通过 `CurrentUser.is_platform_admin` 绕过授权接口。
平台级授权使用固定 domain `__platform__`，由 `AuthorizationService.require_platform()` 返回 `AuthorizationDecision`。
跨租户 SQL 和 repository 入口只接受这个 decision，不接受调用方传入的裸布尔值。
角色授予和撤销同样只接受 `AuthorizationDecision` 作为授权证明，不能只传 `actor_id`；service 会校验 decision 已允许、scope 匹配目标租户或 platform scope，并且 actor 与 decision user 一致。
高权限写操作统一使用 `assert_authorization_decision()` 校验授权证明，校验项包括：

- decision 必须存在且 `allowed=True`。
- `actor_id` 必须等于 decision 的 `user_id`。
- decision 的 tenant domain 必须等于目标 tenant，或在允许 platform scope 时等于 `__platform__`。
- decision 的 resource/action 必须覆盖当前操作，例如 `manage` 或当前 mutation action。

第一版已接入的强制门禁包括 role grant mutation、tenant lifecycle mutation、user disable 和 session revoke。仅传 `actor_id`、`request_id` 或裸布尔值都不能作为授权证明。
router 层的 `RouteSecurityPolicy.permissions` 使用 `resource:action` 字符串格式。它会强制调用 `app.state.route_authorizer`；如果 route 声明了权限但运行时没有挂载授权器，请求会被拒绝。挂载 `DatabaseRequestSecurityPipeline` 后，route permission 会调用 `AuthorizationService.require()` 校验 `ProjectedPolicy`。
app conformance 会校验 route permission 格式，并拒绝未在 `AppModule.permissions` 声明对应 `PermissionSpec` 的 route policy。
授权通过后，route authorizer 返回的 `AuthorizationDecision` 会写入当前 request state。业务 mutation 可以通过标准 dependency 读取它，并把它作为 service 层的授权证明继续向下传：

```python
from typing import Annotated

from fastapi import Depends

from core.permissions import AuthorizationDecision, route_authorization_decision


async def mutate_workspace(
    decision: Annotated[AuthorizationDecision, Depends(route_authorization_decision)],
) -> dict[str, object]:
    ...
```

如果 handler 声明了 `route_authorization_decision`，但当前 route 未声明权限或 authorizer 没有返回 `AuthorizationDecision`，请求会被拒绝为 `PERMISSION_DENIED`。声明多个 route permissions 时，可用 `route_authorization_decisions()` 读取完整 decision 列表；默认单 decision dependency 返回第一个 decision。

当前实现提供 `AuthorizationService`：

- `authorize()` 查询 `ProjectedPolicy`，返回 `AuthorizationDecision`，不抛异常。
- `require()` 查询并在拒绝时抛 `PERMISSION_DENIED`。
- `authorize_platform()` / `require_platform()` 使用 `__platform__` domain 查询 platform scope 投影。
- 拒绝时如果传入 `AuditService`，会在同一个数据库 session 中写入 `authorization.denied` 审计。
- `DatabaseRequestSecurityPipeline(audit_factory=...)` 会把 route permission 拒绝审计持久化，用于 HTTP 入口的权限拒绝追踪。
- 第一版 subject 固定为 `user:{user_id}`，tenant domain 固定为 `tenant_id`。
- 业务 app 不直接查询 `ProjectedPolicy`；文件、任务、业务资源等入口应接入 `AuthorizationService`。
- `platform_apps.files.AuthorizationServiceFileResourceAdapter` 复用 `AuthorizationService`，把文件 `upload/download/delete` 映射到 owner resource 的 `write/read/write` 实例权限，并保留 tenant/owner 归属校验。

## 权限点注册

每个 app 在 `module.py` 中声明权限点：

```python
permissions=[
    PermissionSpec(resource="workspace", action="read", scope="tenant"),
    PermissionSpec(resource="workspace", action="write", scope="tenant"),
    PermissionSpec(resource="file", action="upload", scope="tenant"),
]
```

core 启动时可收集权限点用于初始化、校验和后台展示。
权限目录同时会收集 `AppModule` 中的 admin metadata，并把 `AdminPermissionSpec` 转换为 `resource=admin:<resource>`、`scope=platform` 的 `PermissionSpec`。

CLI 可查看权限目录：

```bash
core permissions catalog --installed-app apps.example_domain.module --json
core permissions reconcile --installed-app apps.example_domain.module --json
core permissions reconcile --database-url sqlite+aiosqlite:///./data/local.db --json
core permissions reconcile --database-url sqlite+aiosqlite:///./data/local.db --repair --json
```

`catalog` 来自 app module 的 `PermissionSpec` 和 admin metadata 转换后的平台权限。
`reconcile` 有两种模式：

- 不传 `--database-url` 时运行 metadata mode，用于部署前检查权限目录是否可收集。
- 传 `--database-url` 时运行 projection mode，调用 `PolicyProjector.reconcile()` 检测 RoleGrant/RoleTemplate 与 ProjectedPolicy 的 drift。
- projection mode 只有显式传 `--repair` 时才会修复 missing/stale policy，并提交事务。

RoleGrant 投影变更会失效 `PermissionCache`：投影 handler 更新或删除 ProjectedPolicy 后立即 invalidate；角色撤销会在 outbox handler 消费前同步删除已有 ProjectedPolicy 并失效缓存，避免撤销窗口继续使用旧授权结果。

## 角色建议

第一版内置：

```text
owner
admin
editor
viewer
```

业务 app 可以额外声明角色模板，但最终都应落到权限点。

## 审计要求

所有授权失败必须记录：

- user_id
- tenant_id
- resource
- action
- request_id
- route
