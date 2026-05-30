# CLI Guide（本地与运维命令说明）

本页面向“第一次使用者”，所有参数都按可复制命令给出。
建议默认加 `--json`，方便脚本判读。

## 1）通用约定

- `core` 命令返回码：
  - `0`：成功
  - `1`：运行时失败
  - `2`：参数/使用错误
- 带 `--json` 时，常见字段：
  - `ok`：布尔
  - `command`：命令路径
  - `error.code`：错误码（失败时）
  - `error.message`：错误详情

## 2）应用命令（app）

### `core bootstrap-app <label>`

参数：
- `label`（必填）：模块名，如 `books`
- `--target-root`（默认 `src`）
- `--package`（默认 `apps`）
- `--json`

示例：

```bash
core bootstrap-app books --target-root src --package platform_apps --json
```

### `core check-app [module_path]`

参数：
- `--all`：检查所有已安装模块
- `--installed-app`：覆盖安装模块列表，可重复
- `--json`

示例：

```bash
core check-app platform_apps.notes.module --json
core check-app --all --json
core check-app --installed-app platform_apps.platform_accounts.module --json
```

### `core list-apps`

参数：
- `--installed-app`：覆盖安装模块列表，可重复
- `--json`

示例：

```bash
core list-apps --json
core list-apps --installed-app platform_apps.platform_accounts.platform_tenants.module --json
```

## 3）配置类命令（config）

### `core config template`
- `--profile {local|private|cloud}`
- `--json`

### `core config drift-check`
- `--profile {local|private|cloud}`（必填）
- `--role`（可选，值为 `server|worker|scheduler|outbox-dispatcher|migrate`）
- `--actual KEY=VALUE`（可重复）
- `--json`

### `core config artifacts`
- `--profile {local|private|cloud}`（必填）
- `--target {docker-compose|systemd|helm-values}`（必填）
- `--role`
- `--actual KEY=VALUE`（可重复）
- `--json`

示例：

```bash
core config template --profile local --json
core config drift-check --profile local --json
core config artifacts --profile local --target docker-compose --json
```

### 其他运行配置命令
- `core check-config --profile local --json`
- `core smoke --profile local --json`
- `core backup-check --profile local --json`

## 4）进程与运行时命令（operations）

### 平台运维命令

```bash
core check-config --profile local --json
core smoke --profile local --json
core backup-check --profile local --latest-backup-at "2026-05-30T00:00:00Z" --max-age-hours 24 --json
```

### 发布检查点

```bash
core release checkpoint \
  --profile local \
  --artifact-target docker-compose \
  --installed-app platform_apps.platform_accounts \
  --probe-dependencies \
  --json
```

参数（`core release checkpoint`）：
- `--profile`
- `--artifact-target {docker-compose|systemd|helm-values}`
- `--actual KEY=VALUE`（可重复）
- `--role-actual ROLE:KEY=VALUE`（可重复，ROLE 见 `server|worker|scheduler|outbox-dispatcher|migrate`）
- `--latest-backup-at`
- `--max-age-hours`
- `--installed-app`（可重复）
- `--probe-dependencies`

### 服务进程

#### `core serve`
- `--dry-run`：仅预检
- `--run`：实际启动
- `--host`（默认 `0.0.0.0`）
- `--port`（默认 `8000`）
- `--reload`（开发模式）
- `--workers`（默认 `1`）
- `--installed-app`（可重复）
- `--database-url`
- `--json`

```bash
core serve --dry-run --host 127.0.0.1 --port 8000 --json
core serve --run --host 127.0.0.1 --port 8000 --reload --json
```

#### `core worker`
- `--run` / `--run-once`
- `--database-url`
- `--installed-app`（可重复）
- `--queue`（默认 `default`）
- `--provider`：`sync` / `database`
- `--max-attempts`
- `--retry-backoff-seconds`
- `--tenant-status`（默认 `active`）
- `--instance-id`
- `--max-iterations`
- `--idle-sleep-seconds`（默认 `1.0`）
- `--json`

```bash
core worker --run-once --provider database --database-url "$DATABASE__URL" --json
core worker --run --queue default --tenant-status active --json
```

#### `core scheduler`
- `--run` / `--run-once`
- `--tenant-id`（`--run-once` 和 `--run` 都建议带）
- `--schedule-id`（`--run-once` 下必填）
- `--database-url`
- `--installed-app`（可重复）
- `--provider`：`local` / `apscheduler` / `celery_beat`
- `--tenant-status`（默认 `active`）
- `--planned-at`（ISO 时间）
- `--now`（ISO 时间）
- `--payload-json`（默认 `{}`）
- `--request-id`（默认 `scheduler-run-once`）
- `--request-id-prefix`（默认 `scheduler-run`）
- `--instance-id`
- `--max-iterations`
- `--idle-sleep-seconds`
- `--lock-ttl-seconds`
- `--json`

```bash
core scheduler --run-once --tenant-id tenant_demo --schedule-id daily-summary --json
core scheduler --run --tenant-id tenant_demo --provider local --json
```

#### `core outbox-dispatcher`
- `--run`
- `--database-url`
- `--installed-app`（可重复）
- `--dispatcher-id`（默认 `outbox-dispatcher`）
- `--instance-id`
- `--batch-size`（默认 `20`）
- `--max-iterations`
- `--idle-sleep-seconds`
- `--json`

```bash
core outbox-dispatcher --run --database-url "$DATABASE__URL" --batch-size 50 --json
```

## 5）权限命令

### `core permissions catalog`
- `--installed-app`（可重复）
- `--json`

### `core permissions reconcile`
- `--installed-app`（可重复）
- `--database-url`（不传则仅读元数据）
- `--repair`（启用持久化）
- `--json`

```bash
core permissions catalog --installed-app platform_apps.platform_tenants.module --json
core permissions reconcile --installed-app platform_apps.platform_tenants.module --database-url "$DATABASE__URL" --repair --json
```

## 6）迁移命令

### `core migrate plan | preflight | dry-run | status`
- `--installed-app`（可重复）
- `--phase {expand|backfill|contract|maintenance}`（非 status）
- `--backup-ready`（preflight/dry-run）
- `--json`

### `core migrate apply`
- `--installed-app`
- `--phase`
- `--backup-ready`
- `--yes`（**必需**，否则拒绝执行）
- `--alembic-config`
- `--database-url`
- `--script-location`
- `--lock-owner`
- `--lock-ttl-seconds`（默认 `300`）
- `--json`

### `core migrate run`
- `--apply`（是否在 `dry-run` 之外执行）
- `--yes`（搭配 `--apply` 更安全）
- `--installed-app`
- `--phase`
- `--backup-ready`
- `--alembic-config`
- `--database-url`
- `--script-location`
- `--lock-owner`
- `--lock-ttl-seconds`
- `--json`

### `core migrate drift-check`
- `--expected app=head`（可重复）
- `--actual app=head`（可重复）
- `--json`

示例：

```bash
core migrate plan --phase expand --json
core migrate preflight --phase expand --backup-ready --json
core migrate dry-run --phase contract --json
core migrate status --json
core migrate run --phase expand --apply --yes --json
core migrate apply --phase maintenance --yes --json
core migrate drift-check --expected users=abc123 --actual users=def456 --json
```

## 7）幂等、任务、死信队列

### idempotency

- `core idempotency diagnose`
  - `--database-url`
  - `--tenant-id`（必填）
  - `--user-id`（必填）
  - `--route`（必填）
  - `--idempotency-key`（必填）
  - `--request-hash`
  - `--now`
  - `--retry-failed`
  - `--json`
- `core idempotency expire`
  - `--database-url`
  - `--now`
  - `--yes`（必需）
  - `--json`

### tasks

- `core tasks failed list`
  - `--database-url`
  - `--limit`
  - `--json`
- `core tasks failed retry`
  - `--database-url`
  - `--installed-app`（可重复）
  - `--task-id`（必填）
  - `--yes`（必需）
  - `--json`
- `core tasks running recover`
  - `--database-url`
  - `--older-than-seconds`（必填）
  - `--limit`
  - `--yes`（必需）
  - `--json`

### outbox

- `core outbox dispatch-once`
  - `--database-url`
  - `--installed-app`（可重复）
  - `--dispatcher-id`（默认 `outbox-dispatcher`）
  - `--batch-size`
  - `--json`
- `core outbox dead-letter list`
  - `--database-url`
  - `--limit`
  - `--json`
- `core outbox dead-letter replay`
  - `--database-url`
  - `--event-id`（必填）
  - `--yes`（必需）
  - `--json`

示例：

```bash
core idempotency diagnose --tenant-id tenant_demo --user-id user_1 --route /api/v1/books --idempotency-key req-001 --json
core idempotency expire --yes --json
core tasks failed list --limit 20 --json
core tasks failed retry --task-id task_123 --yes --json
core outbox dead-letter list --limit 20 --json
core outbox dead-letter replay --event-id evt_001 --yes --json
```

## 8）给新手的日常“自检脚本”建议

```bash
core config drift-check --profile local --json
core check-config --profile local --json
core check-app --all --json
core permissions catalog --json
core permissions reconcile --database-url "$DATABASE__URL" --json
core migrate plan --json
core serve --dry-run --json
core smoke --profile local --json
```
