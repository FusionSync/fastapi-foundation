# Core Cache

## 职责

Cache 模块提供统一缓存抽象，屏蔽内存缓存和 Redis 的差异。

## 目录建议

```text
src/core/cache/
  provider.py
  memory.py
  redis.py
  keys.py
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
```

## 设计要求

- 业务 app 不直接依赖 Redis client。
- key 生成集中管理，避免散落字符串。
- 所有缓存必须有 TTL，除非明确声明为永久。
- 缓存不可作为唯一事实来源。

## 当前实现

已落地 `CacheProvider`、`MemoryCacheProvider` 和 `cache_key()`：

- `MemoryCacheProvider` 支持 `get`、`set`、`delete`、`exists`、`incr`、`expire`、`get_json`、`set_json`。
- `set`、`set_json` 和首次 `incr` 默认必须提供 `ttl_seconds`；只有显式 `permanent=True` 才允许无 TTL。
- 过期 key 在读取、删除、续期和加计数时惰性清理。
- `cache_key(namespace, *parts)` 统一生成 `:` 分隔 key，并拒绝空片段和包含 `:` 的片段，避免 key 语义歧义。
- 内存 provider 只用于 local profile、测试和单机版；private/cloud profile 后续必须接 Redis 或等价 provider。

后续 rate limit、OIDC JWKS、tenant settings 和 task short status 都应依赖 `CacheProvider`，不能直接依赖 Redis client。
