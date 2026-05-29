# Core Tenancy

## Progress

- Status: `connected`
- Done: tenant model、member model、invitation model、resolver、lifecycle service、membership 校验、租户上下文、outbox-backed lifecycle/invitation/member events、membership/cache invalidation 与权限投影联动、route/task/file download/background cleanup lifecycle gate 已落地。
- Next: _none_

## 职责

Tenancy 模块负责解析当前租户、维护租户上下文，并向业务查询和权限校验提供 `tenant_id`。

## 租户来源

第一版支持：

- Header：`X-Tenant-Id`
- Token claim：OIDC/Logto/Keycloak 中的 organization id
- 用户默认租户：用于本地单机版

Header 只能作为“租户选择器”，不能直接成为可信租户上下文。

## 租户解析规则

`DatabaseTenantContextResolver` 是请求/服务层入口：它接收 `core.auth.CurrentUser`，从数据库读取 `Tenant` 和 `TenantMember` 事实，再调用 `resolve_current_tenant()` 完成规则校验。`resolve_current_tenant()` 保持为纯规则函数，用于测试和非数据库场景，不允许业务层手拼 memberships 作为可信来源。

顺序和门禁如下：

```text
1. 解析认证主体，得到 current_user。
2. 从 token/session tenant claim、X-Tenant-Id、用户默认租户中选择目标租户。
3. 如果 header 与 token organization claim 冲突，返回 403 + TENANT_CONTEXT_CONFLICT。
4. 从数据库读取 TenantMember，用户不是该租户 active member 时返回 403 + TENANT_ACCESS_DENIED。
5. 从数据库读取 Tenant；不能在 resolver 中把未加载租户默认视为 active。
6. 如果租户状态不是当前操作允许状态，返回 403 + TENANT_STATE_FORBIDDEN。
7. 写入冻结后的 RequestContext.tenant_id。
```

规则：

- 未认证请求不能通过 `X-Tenant-Id` 建立租户上下文。
- 多租户用户必须显式选择或使用默认租户；选择结果必须与 `TenantMember(active)` 匹配。
- platform scope 操作必须显式声明 cross-tenant intent，不复用普通租户上下文。
- 租户解析失败、冲突和跨租户访问都必须写安全审计。

## Membership 联动

租户成员关系是租户访问事实，不承载角色事实。成员激活和角色授予必须分别写入 outbox：

- 接受邀请时先激活 `TenantMember(active)`，写入 `tenant.member_activated` 事件。
- 如果邀请绑定初始角色，再写入 `RoleGrant` 事实和 `permissions.role_grant_changed` 事件。
- `tenant.member_activated` 通过 `core.cache.register_cache_invalidation_handlers()` 清理该用户的 membership cache 和 permission subject cache。
- `permissions.role_grant_changed` 继续驱动 `PolicyProjector` 更新 `ProjectedPolicy`，事件 payload 必须携带 `subject_type` / `subject_id`，保证缓存失效能定位到同一用户。

## 目录建议

```text
src/core/tenancy/
  context.py
  deps.py
  resolver.py
```

## Tenant Context

```text
tenant_id
tenant_code
tenant_name
deployment_mode
```

业务 service 必须从上下文获取当前租户：

```python
tenant = get_current_tenant()
```

## 隔离策略

第一版采用 shared database + shared schema：

```text
每张业务表带 tenant_id
所有查询显式过滤 tenant_id
权限校验包含 tenant_id
文件路径或对象 key 包含 tenant_id
```

不采用 schema-per-tenant，避免迁移、运维和私有化复杂度过高。

## 风险控制

- 列表查询必须默认租户过滤。
- 文件下载必须校验文件所属租户。
- 后台管理跨租户接口必须走 platform admin 权限。
- 审计日志必须记录 tenant_id。
- 租户生命周期状态必须在 route dependency、repository、task、file download 和 background cleanup gate 中统一执行。
