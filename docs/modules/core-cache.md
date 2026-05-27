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
