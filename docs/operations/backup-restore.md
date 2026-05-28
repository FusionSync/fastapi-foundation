# Backup And Restore

## 目标

备份恢复文档定义 PostgreSQL、对象存储、审计日志、配置和密钥的恢复要求。迁移、租户删除、私有化升级和公网 SaaS 运维都必须以这里的能力为前提。

## 备份对象

```text
PostgreSQL
  tenants, users, memberships, business tables, outbox, tasks, audit metadata

Object storage
  uploaded files, generated exports, archived tenant data

Audit records
  append-only audit table, optional WORM/SIEM export

Configuration
  deployment profile, feature flags, app registry settings

Secrets
  JWT keys, OIDC client secrets, storage credentials, database passwords
```

## RPO / RTO

初始目标：

```text
local:
  best effort

private:
  RPO <= 24h unless customer requires stricter
  RTO <= 8h

cloud:
  RPO <= 1h
  RTO <= 4h
```

具体项目可以收紧目标，但不能在生产 profile 中没有 RPO/RTO。

## PostgreSQL

要求：

- 周期性全量备份。
- WAL/PITR 能力按 profile 开启。
- 迁移前必须检查最近备份是否可用。
- destructive 或 `requires_backup_restore` migration 必须记录备份点。
- 定期恢复演练到隔离环境。

## Object Storage

要求：

- object key 必须包含 tenant 或可由 FileObject 反查 tenant。
- 备份必须覆盖 bucket lifecycle、加密配置和对象元数据。
- 孤儿对象清理由对账任务执行，不能只删数据库记录。
- 租户归档导出必须包含 FileObject metadata 和对象数据引用。

## Audit

要求：

- 生产审计记录不能随业务软删除。
- 安全关键审计建议启用 hash chain 或外部 WORM/SIEM。
- 审计导出必须记录导出者、时间、过滤条件和文件校验值。

## Restore Types

```text
full restore
  恢复整个环境

point-in-time restore
  恢复到指定时间点

tenant restore
  从备份中恢复单租户数据到隔离环境，人工校验后再导入

object restore
  恢复单个或一组文件对象
```

第一版可以只实现 full restore 和 object restore 的运行手册，但数据模型和审计必须预留 tenant restore 所需的 tenant_id 和时间戳。

## 验证

恢复演练完成后必须验证：

- `core migrate status` 正常。
- `core migrate drift-check` 无意外 drift。
- `/readyz` 通过。
- 文件下载校验 checksum。
- 关键租户、用户、权限、审计数据可查询。
- outbox 不重复执行已完成的外部副作用。
