# Core Tenancy

## 职责

Tenancy 模块负责解析当前租户、维护租户上下文，并向业务查询和权限校验提供 `tenant_id`。

## 租户来源

第一版支持：

- Header：`X-Tenant-Id`
- Token claim：OIDC/Logto/Keycloak 中的 organization id
- 用户默认租户：用于本地单机版

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
