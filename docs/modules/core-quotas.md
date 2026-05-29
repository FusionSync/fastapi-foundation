# Core Quotas

## Progress

- Status: `connected`
- Done: quota provider、rule、usage 抽象、数据库持久 usage store、文件上传 quota gate、业务 mutation/task submit 统一 quota gate 已落地。
- Next: _none_

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
  gate.py
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

已落地 `QuotaRule`、`QuotaSubject`、`QuotaRegistry`、`QuotaService`、`MemoryQuotaUsageStore`、`DatabaseQuotaUsageStore` 和统一 mutation gate：

- `QuotaRegistry.from_tenant_config()` 可从租户配置生成 metric 规则。
- `QuotaRule` 声明 `metric`、`limit` 和 `scope`，scope 支持 `tenant`、`user`、`resource`。
- `QuotaSubject` 统一生成 tenant/user/resource 维度 key。
- `QuotaService.check()` 只读检查，不改变用量，适合展示剩余额度。
- `QuotaService.reserve()` 执行强校验并在通过时增加用量，适合文件上传、创建用户、提交任务等关键写操作。
- `QuotaService.require_reserve()` 在配额不足时抛 `QUOTA_EXCEEDED`，并带稳定 details。
- `QuotaService.release()` 支持释放并发类配额，例如 `concurrent_tasks`。
- `DatabaseQuotaUsageStore` 使用 `quota_usage` 表持久化 usage key 和 used 值；`reserve()` 通过数据库条件更新实现 check-and-increment，超限时不增加用量。
- `FileService.upload_bytes()` 可注入 `QuotaService` 和上传 quota rule，在写 storage 前 reserve；任一 quota 失败或后续写入失败时会释放已 reserve 的上传 quota。
- `QuotaReservation` 描述一次强校验 reservation，包含 rule、subject 和 amount。
- `QuotaMutationGate.run_mutation()` 用于业务写操作；任一 reservation 失败时不会调用 handler，handler 异常时会反向释放已 reserve 的用量。
- `QuotaMutationGate.submit_task()` 用于任务提交链路；提交前先 reserve，提交异常时释放，提交成功后保留用量，由任务完成、取消或业务回收路径释放。
- `QuotaTaskSubmitter` 包装符合 `submit(envelope, tenant_status=...)` 契约的 task provider，可在 API、scheduler 或 outbox 提交任务前统一注入配额检查。
- 配额不足时会写可选 `MetricsRegistry` 的 `quota_exceeded_total{metric,scope}`，并可通过 `AuditRecorder` 写 `quota.exceeded` 审计。
- 指标契约已预留 `quota_exceeded_total`。

内存 store 只用于 local profile、测试和单机版。private/cloud profile 应使用 `DatabaseQuotaUsageStore` 或等价 Redis-backed store，并保证 `reserve()` 的 check-and-increment 语义是原子的。
