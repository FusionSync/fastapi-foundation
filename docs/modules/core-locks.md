# Core Locks

## 职责

Locks 模块提供分布式锁和幂等执行保护，避免多实例部署下重复处理同一业务动作。

## 目录建议

```text
src/core/locks/
  provider.py
  redis.py
  memory.py
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
release
extend
locked
```

## 设计要求

- 锁必须有 TTL，禁止永久锁。
- 锁 value 必须包含 owner token，释放时校验 owner。
- 加锁失败要返回统一业务 code。
- 本地单机版可用 memory lock，生产必须用 Redis 或等价实现。
