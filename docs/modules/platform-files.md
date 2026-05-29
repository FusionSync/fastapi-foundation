# Platform App: Files

## Progress

- Status: `partial`
- Done: file metadata、local/S3 storage provider、owner/authorization gate、upload quota gate、virus scan gate、delete retention cleanup、upload/download/delete 权限和基础 storage tests 已落地。
- Next:
  - [ ] 将业务资源级文件权限替换 owner-only 最小门禁。

## 职责

Files 负责文件元数据、上传、下载、版本和 storage provider 调用。
它通过 `platform_apps.files.module` 暴露 `AppModule`，统一注册模型、权限、迁移包、router 和 public_api。

## 核心模型

```text
FileObject
  id
  tenant_id
  owner_type
  owner_id
  bucket
  object_key
  file_name
  content_type
  size
  checksum
  file_type
  status
  created_at
  deleted_at
```

## 文件类型

```text
upload
import
export
image
attachment
temporary
```

## API

```text
POST /api/v1/files/upload
GET  /api/v1/files/{id}
GET  /api/v1/files/{id}/download
DELETE /api/v1/files/{id}
```

## 设计要求

- 所有文件必须落库。
- 文件上传、下载、删除必须检查租户/owner 上下文和 `file.*` 权限，默认拒绝未显式授权的调用。
- 业务 app 通过 file id 引用文件，不直接持有存储路径。
- 后续支持文件版本、病毒扫描和生命周期清理。

## 当前实现

第一版落点：

- `platform_apps.files.models.FileObject` 保存文件 metadata，不依赖具体 storage provider。
- `FileService.upload_bytes()` 必须传入 `AuthorizationService` 和 `user_id`，先要求 `file.upload` 权限，再经过 `core.security.UploadSecurityPolicy` 和可插拔 `FileVirusScanner`，最后写 storage 和 FileObject metadata。
- `FileService.upload_bytes()` 可注入 `QuotaService` 和 upload quota rules；storage 写入前会 reserve quota，超限时不写对象、不落 metadata，部分 reserve 失败时会释放已扣用量。
- `FileService.download_bytes()` 执行 tenant lifecycle `file_download` gate，再校验 tenant、owner_type、owner_id，并要求 `file.download` 权限后读取 storage。
- `FileService.delete_file()` 校验 tenant、owner_type、owner_id，并要求 `file.delete` 权限后，将 metadata 标记为 `deleted`；未配置 retention 时立即删除 storage object，配置 `delete_retention_seconds` 时保留对象到后台清理。
- `FileService.purge_deleted_files()` 执行 tenant lifecycle `background_cleanup` gate，只清理 retention 到期的 `deleted` 文件对象，并将 metadata 标记为 `purged`。
- 文件权限拒绝时复用权限模块的 `authorization.denied` 审计；缺少 `AuthorizationService` 时直接返回 `PERMISSION_DENIED`，避免服务层绕过 route 权限。
- 上传对象 key 使用 `tenants/{tenant_id}/files/{file_id}/original.bin`，保证对象存储可按 tenant 归档和恢复。
- `platform_apps.files.permissions.PERMISSIONS` 注册 `file.upload`、`file.download`、`file.delete` 租户权限。

当前 owner 校验是权限接入前的最小门禁。已接入的 `file.upload`、`file.download`、`file.delete` 授权用于平台文件权限；业务资源级文件操作后续可以在同一入口改用对应业务资源权限。
