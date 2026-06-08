# 模块文档索引

## Progress 使用规则

- 每个模块文档的 `## Progress` 是该模块的进度事实源。
- Status 含义：`connected` 表示最小运行链路和契约测试已接入；`partial` 表示核心 API 或 provider 已落地但大功能还未全部闭环；`planned` 表示仍以设计为主。
- 推进顺序必须服从 [Foundation Roadmap](../architecture/01-foundation-roadmap.md) 的大功能 checkpoint；不要在 checkpoint 完成前反复优化单个模块细节。
- 每次完成一个大功能后，同步更新相关模块的 Done/Next 列表，再运行该大功能的集中验证。

## Core 模块

- [App Runtime](core-app-runtime.md)
- [App Registry](core-app-registry.md)
- [Base Classes](core-base-classes.md)
- [Config](core-config.md)
- [Context](core-context.md)
- [Database](core-database.md)
- [Tenant Isolation](core-tenant-isolation.md)
- [Migrations](core-migrations.md)
- [Auth](core-auth.md)
- [Security](core-security.md)
- [Tenancy](core-tenancy.md)
- [Permissions](core-permissions.md)
- [Permission Model](core-permission-model.md)
- [Cache](core-cache.md)
- [Locks](core-locks.md)
- [Idempotency](core-idempotency.md)
- [Rate Limit](core-rate-limit.md)
- [Quotas](core-quotas.md)
- [Storage](core-storage.md)
- [HTTP Clients](core-http-clients.md)
- [Tasks](core-tasks.md)
- [Scheduler](core-scheduler.md)
- [Events](core-events.md)
- [Transactional Outbox](core-outbox.md)
- [Exceptions](core-exceptions.md)
- [Serialization](core-serialization.md)
- [Messages](core-messages.md)
- [MQ](core-mq.md)
- [Admin Registry](core-admin-registry.md)
- [CLI](core-cli.md)
- [Testing](core-testing.md)
- [API Conventions](core-api-conventions.md)
- [Observability](core-observability.md)

## Platform Apps

- [Accounts](platform-accounts.md)
- [Tenants](platform-tenants.md)
- [Tenant Lifecycle](platform-tenant-lifecycle.md)
- [Files](platform-files.md)
- [Audit](platform-audit.md)

## App 开发规范

- [App Module Contract](app-module-contract.md)
- [App Development Guide](app-development-guide.md)
