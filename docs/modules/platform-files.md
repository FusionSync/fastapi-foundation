# Platform App: Files

## 职责

Files 负责文件元数据、上传、下载、版本和 storage provider 调用。

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
- 文件下载必须检查租户、owner 和权限。
- 业务 app 通过 file id 引用文件，不直接持有存储路径。
- 后续支持文件版本、病毒扫描和生命周期清理。

## 当前实现

第一版落点：

- `platform_apps.files.models.FileObject` 保存文件 metadata，不依赖具体 storage provider。
- `FileService.upload_bytes()` 先写 storage，再写 FileObject metadata，并记录 bucket、object_key、size、checksum。
- `FileService.download_bytes()` 执行 tenant lifecycle `file_download` gate，再校验 tenant、owner_type、owner_id。
- 下载调用方可以传入 `AuthorizationService` 和 `user_id`，进一步要求 `file.download` 权限；拒绝时会复用权限模块的 `authorization.denied` 审计。
- `FileService.delete_file()` 将 metadata 标记为 `deleted`，并删除 storage object。
- 上传对象 key 使用 `tenants/{tenant_id}/files/{file_id}/original.bin`，保证对象存储可按 tenant 归档和恢复。

当前 owner 校验是权限接入前的最小门禁。已接入的 `file.download` 授权用于平台文件权限；业务资源级文件下载后续可以在同一入口改用对应业务资源权限。
