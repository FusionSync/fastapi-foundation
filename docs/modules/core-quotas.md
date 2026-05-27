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
