# Core Quotas

## 职责

Quotas 模块负责租户级、用户级或资源级配额控制。它回答“还能不能继续使用资源”。

## 与 Rate Limit 的区别

```text
Rate Limit
  控制单位时间内的请求频率。

Quotas
  控制总量、用量、并发量和套餐额度。
```

## 目录建议

```text
src/core/quotas/
  provider.py
  rules.py
  usage.py
  deps.py
```

## 配额类型

```text
storage_bytes
monthly_api_calls
active_users
concurrent_tasks
file_count
```

## 使用场景

- SaaS 套餐限制。
- 私有化客户资源保护。
- 单机版防止误操作塞满磁盘。
- 控制后台任务并发。

## 设计要求

- 配额规则从租户配置读取。
- 用量统计可以延迟，但关键写操作必须强校验。
- 配额不足返回 `QUOTA_EXCEEDED`。
- 配额命中必须进入审计和指标。

## 当前实现

已落地 `QuotaRule`、`QuotaSubject`、`QuotaRegistry`、`QuotaService` 和 `MemoryQuotaUsageStore`：

- `QuotaRegistry.from_tenant_config()` 可从租户配置生成 metric 规则。
- `QuotaRule` 声明 `metric`、`limit` 和 `scope`，scope 支持 `tenant`、`user`、`resource`。
- `QuotaSubject` 统一生成 tenant/user/resource 维度 key。
- `QuotaService.check()` 只读检查，不改变用量，适合展示剩余额度。
- `QuotaService.reserve()` 执行强校验并在通过时增加用量，适合文件上传、创建用户、提交任务等关键写操作。
- `QuotaService.require_reserve()` 在配额不足时抛 `QUOTA_EXCEEDED`，并带稳定 details。
- `QuotaService.release()` 支持释放并发类配额，例如 `concurrent_tasks`。
- 配额不足时可通过 `AuditRecorder` 写 `quota.exceeded` 审计。
- 指标契约已预留 `quota_exceeded_total`。

内存 store 只用于 local profile、测试和单机版。private/cloud profile 后续应替换为数据库或 Redis-backed store，并保证 `reserve()` 的 check-and-increment 语义是原子的。
