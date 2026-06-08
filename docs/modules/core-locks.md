# Core Locks

## Progress

- Status: `connected`
- Done: lock provider 抽象、内存实现、数据库表实现、Redis provider、Redis runtime 装配、scheduler trigger 并发保护，以及 outbox/migration/audit hash chain 跨进程锁使用点已落地。
- Next: 无。

## 职责

Locks 模块提供分布式锁和幂等执行保护，避免多实例部署下重复处理同一业务动作。

## 目录建议

```text
src/core/locks/
  provider.py
  redis.py
  database.py
  memory.py
  models.py
  decorators.py
```

## 使用场景

- 防止同一任务重复执行。
- 防止重复提交创建资源。
- 防止同一文件被并发处理。
- 控制定时任务只有一个 worker 执行。

## Provider 接口

```text
acquire
require_acquire
release
extend
locked
```

返回值必须包含：

```text
acquired
lock_key
owner_token
expires_at
fencing_token
```

## 设计要求

- 锁必须有 TTL，禁止永久锁。
- 锁 value 必须包含 owner token，释放时校验 owner。
- 加锁失败要返回统一业务 code。
- 本地单机版可用 memory lock，生产必须用 Redis 或等价实现。
- 长任务必须支持续租；续租失败后任务必须停止写入或进入补偿流程。
- 对外部副作用或数据库写入，优先使用幂等记录/唯一约束作为持久保护，锁只作为并发优化。
- 多实例调度使用锁时必须记录 `fencing_token`，避免旧 owner 在锁过期后继续写入。

## 当前实现

已落地 `LockProvider`、`LockHandle`、`MemoryLockProvider` 和 `DatabaseLockProvider`：

- `acquire()` 返回 `LockHandle`，不抛业务冲突；调用方可检查 `acquired`。
- `require_acquire()` 在锁已被持有时抛 `LOCK_NOT_ACQUIRED`，用于 route/dependency 直接转换统一 API 响应。
- `release()` 和 `extend()` 都校验 owner token，错误 owner 不能释放或续租。
- 每次新 owner 成功获取同一 `lock_key` 都递增 `fencing_token`；旧 owner 即使锁过期后继续执行，也可以被下游用 fencing token 拒绝。
- TTL 必填且必须大于 0；没有永久锁。
- 内存 provider 只适合 local profile、测试和单机版。private/cloud profile 必须使用 Redis、数据库 advisory lock 或等价 provider。
- `DatabaseLockProvider` 使用 `core_locks` 表保存 `lock_key`、`owner_token`、`expires_at` 和 `fencing_token`，并通过注入的 `async_sessionmaker` 为 acquire/release/extend 执行独立短事务，避免锁行滞留在调用方长事务中不可见。
- 表锁 provider 适合共享数据库部署的跨进程保护；高并发 PostgreSQL profile 后续可替换为 advisory lock 或 Redis provider，但必须保持同一 `LockProvider` 契约。
- `RedisLockProvider` 通过注入的 async Redis client 工作；当 `DEPENDENCIES__REDIS_URL` 配置后，app runtime 会创建 `redis.asyncio` client，并把 `RedisLockProvider` 挂到 `app.state.lock_provider`。acquire 使用 `SET NX PX` 和独立 fencing key，release/extend 使用 Lua 脚本校验 owner token 后再删除或续租。
- Redis fencing token 单调递增；高竞争下失败尝试可能造成 token 跳号，但成功获取后的 token 仍可用于下游拒绝旧 owner 写入。
- `OutboxDispatcher` 可注入 `LockProvider`，在批量领取前获取 `outbox:dispatch` 粗粒度锁；`apply_migrations()` 在执行真实 migration 前通过注入的 provider 获取 `migrations:apply` 锁；`AuditService` 可注入 provider，为每个 tenant/platform hash chain 获取 `audit:hash-chain:*` 锁并持有到事务结束。

Locks 只解决并发窗口，不替代 `IdempotencyRecord`、业务唯一约束或 outbox handler 幂等。
