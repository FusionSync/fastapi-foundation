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
- `FileService.delete_file()` 将 metadata 标记为 `deleted`，并删除 storage object。
- 上传对象 key 使用 `tenants/{tenant_id}/files/{file_id}/original.bin`，保证对象存储可按 tenant 归档和恢复。

当前 owner 校验是权限接入前的最小门禁。后续接入完整 authorization 时，下载接口应在 tenant/owner 校验后调用 permissions 模块校验 `file.download` 或业务资源权限。
