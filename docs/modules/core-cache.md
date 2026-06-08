# Core Cache

## Progress

- Status: `connected`
- Done: cache provider 抽象、内存实现、Redis provider、Redis runtime 装配、key 约定、权限/租户生命周期/租户成员/outbox 事件驱动缓存失效规则已落地。
- Next: _none_

## 职责

Cache 模块提供统一缓存抽象，屏蔽内存缓存和 Redis 的差异。

## 目录建议

```text
src/core/cache/
  provider.py
  memory.py
  redis.py
  keys.py
  invalidation.py
```

## 使用场景

- OIDC JWKS 缓存。
- 权限策略缓存。
- 租户配置缓存。
- 验证码和一次性 token。
- 限流计数。
- 任务短状态。

## Provider 接口

```text
get
set
delete
exists
incr
expire
get_json
set_json
```

## Key 约定

所有 key 必须带命名空间：

```text
auth:jwks:{issuer}
tenant:{tenant_id}:settings
rate:{tenant_id}:{user_id}:{route}
permission:{tenant_id}
```

## 设计要求

- 业务 app 不直接依赖 Redis client。
- key 生成集中管理，避免散落字符串。
- 所有缓存必须有 TTL，除非明确声明为永久。
- 缓存不可作为唯一事实来源。

## 当前实现

已落地 `CacheProvider`、`MemoryCacheProvider`、`RedisCacheProvider`、`cache_key()` 和事件驱动缓存失效：

- `MemoryCacheProvider` 支持 `get`、`set`、`delete`、`exists`、`incr`、`expire`、`get_json`、`set_json`。
- `RedisCacheProvider` 通过注入的 async Redis client 工作；当 `DEPENDENCIES__REDIS_URL` 配置后，app runtime 会创建 `redis.asyncio` client，并把 `RedisCacheProvider` 挂到 `app.state.cache_provider`。
- Redis runtime 会在 `/readyz` 中执行 `PING` 探活，startup diagnostics 会输出脱敏 Redis URL、cache provider 和 lock provider。
- `set`、`set_json` 和首次 `incr` 默认必须提供 `ttl_seconds`；只有显式 `permanent=True` 才允许无 TTL。
- 过期 key 在读取、删除、续期和加计数时惰性清理。
- `cache_key(namespace, *parts)` 统一生成 `:` 分隔 key，并拒绝空片段和包含 `:` 的片段，避免 key 语义歧义。
- `tenant_settings_cache_key()`、`tenant_lifecycle_cache_key()`、`tenant_membership_cache_key()`、`permission_cache_key()`、`permission_subject_cache_key()` 和 `permission_role_grant_cache_key()` 提供权限/租户缓存的稳定 key 模板。
- `CacheInvalidationRule` 描述 event_type/version 到 cache keys 的映射；默认规则覆盖 `permissions.role_grant_changed`、`tenant.member_activated` 和 `tenant.created/suspended/reactivated/deleting/archived/deleted`。
- `CacheInvalidationHandler` 可作为 outbox event handler 注册，收到权限事实或租户生命周期事件后删除匹配 key，并返回 deleted/missing key 结果用于测试和诊断。
- `register_cache_invalidation_handlers()` 会把默认规则注册进 `EventRegistry`，让 outbox dispatcher 通过同一事件分发链路触发缓存失效。
- 内存 provider 只用于 local profile、测试和单机版；private/cloud profile 必须配置 Redis 或等价 provider。

后续 rate limit、OIDC JWKS、tenant settings 和 task short status 都应依赖 `CacheProvider`，不能直接依赖 Redis client。
