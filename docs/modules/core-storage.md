# Core Storage

## Progress

- Status: `partial`
- Done: storage provider、local backend、S3/MinIO-compatible backend、key path 规则和 platform files permission/quota/virus scan/retention cleanup 接入点已落地。
- Next:
  - [ ] 将业务资源级 permission adapter 串进文件访问链路。

## 职责

Storage 模块负责文件存储抽象，屏蔽本地文件系统、MinIO 和 S3 的差异。

## 目录建议

```text
src/core/storage/
  provider.py
  local.py
  s3.py
  paths.py
```

## 存储对象

系统需要管理：

- 用户上传文件
- 导入文件
- 导出文件
- 图片和附件
- 任务中间产物
- 系统生成文件

## Storage Provider 接口

```text
put_file
get_file
open_read
open_write
delete_file
generate_download_url
```

业务 app 不直接访问磁盘路径或 S3 SDK。

## Key 设计

对象 key 必须包含租户上下文和资源上下文：

```text
tenants/{tenant_id}/files/{file_id}/original.bin
tenants/{tenant_id}/resources/{resource_type}/{resource_id}/{file_id}.bin
```

## 本地与云端

```text
local:
  ./data/files

private:
  MinIO 或内网对象存储

cloud:
  S3 兼容对象存储
```

## 安全要求

- 下载接口必须经过权限校验。
- 私有文件不直接暴露公共 URL。
- 临时下载 URL 必须有过期时间。
- 文件记录必须落库，不能只依赖对象存储列表。

## 当前实现

第一版先提供 provider 抽象和 local provider：

- `StorageProvider` 定义 `put_file`、`get_file`、`delete_file`、`exists`、`generate_download_url`。
- `LocalStorageProvider` 用于 local/profile 和测试环境，写入本地目录。
- `S3StorageProvider` 通过注入的 async S3-compatible client 工作，可用于 MinIO/S3；core 不直接强绑定对象存储 SDK。
- local provider 会校验 object key，拒绝绝对路径和 `..` 路径穿越。
- S3 provider 复用同样 object key 校验，并通过 client 生成私有对象的临时下载 URL。
- `file_object_key()` 固定上传原始文件 key：`tenants/{tenant_id}/files/{file_id}/original.bin`。
- `resource_object_key()` 固定资源关联文件 key：`tenants/{tenant_id}/resources/{resource_type}/{resource_id}/{file_id}.bin`。
- platform files 上传链路已在写 storage 前接入 permission、upload security、virus scan 和 quota gate。
- platform files 删除链路支持 retention window；对象清理由 tenant lifecycle `background_cleanup` gate 保护，过期后再 purge storage object。

后续扩展 provider 或业务资源级文件权限时，必须沿用相同 object key 约定，业务 app 只能通过 provider 和 FileObject metadata 访问文件。
