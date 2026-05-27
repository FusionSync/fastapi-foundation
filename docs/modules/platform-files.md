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
