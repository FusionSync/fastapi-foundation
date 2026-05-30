# Tenant Isolation (对外)

多租户隔离采用默认仓储作用域 + `tenant_id` 上下文 + raw SQL 守卫。

- TenantScopedModel 默认要求。
- 未经授权不得执行跨租户访问。
- 跨租户必须携带 `reason` + `platform decision`。
