# Operations Runbook

## 常用命令角色

```text
core serve
core worker
core scheduler
core outbox-dispatcher
core migrate plan
core migrate preflight
core migrate dry-run
core migrate apply
core migrate status
core migrate drift-check
core tasks failed list
core tasks failed retry
core check-config
core check-app
```

## 发布前检查

- `core check-config --profile <profile> --json`
- `core check-app --all --json`
- `core migrate plan --json`
- `core migrate preflight --json`
- `core migrate apply --alembic-config <path> --database-url <url> --yes --json`
- 备份可用性检查。
- 确认 outbox dead letter 和 task failed 数量在可接受范围内。

## 故障处理入口

- API 错误率升高：查看 HTTP status、app code、route、trace_id。
- 租户越权告警：暂停相关租户写入，检查 tenant resolver、repository guard、审计记录。
- outbox 堆积：检查 dispatcher health、dead letter、handler error、数据库锁。
- task 堆积：检查 worker health、queue latency、tenant lifecycle gate，用 `core tasks failed list --json` 定位失败任务，确认后用 `core tasks failed retry --task-id <id> --yes --json` 重试。
- 迁移失败：停止后续发布步骤，按 migration classification 执行 forward fix 或 restore。
