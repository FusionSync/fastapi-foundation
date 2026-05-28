# Core Security

## 职责

Security 模块负责认证之外的底层安全能力，包括密码、密钥、请求安全、文件安全和敏感信息保护。

## 目录建议

```text
src/core/security/
  password.py
  secrets.py
  cors.py
  trusted_hosts.py
  upload.py
  masking.py
```

## 核心能力

- 密码哈希和校验。
- JWT secret 和外部密钥读取。
- CORS 和 Trusted Host 配置。
- 请求体大小限制。
- 上传文件大小、扩展名、MIME、checksum 校验。
- 敏感字段脱敏。
- 安全响应头。

## 设计要求

- Auth 模块不直接实现密码算法，调用 Security。
- 文件上传必须经过 Security 校验后才能进入 Storage。
- 日志、审计、异常 details 必须先脱敏。
- 生产环境启动时必须执行安全配置检查。

## 当前实现

已落地密码哈希、敏感字段脱敏、上传安全校验和安全响应头：

- `PasswordHasher` 使用 PBKDF2-SHA256，默认随机 salt，并提供常量时间校验。
- `redact_sensitive_data()` 递归脱敏 password、token、secret、authorization、cookie 等敏感字段。
- `UploadSecurityPolicy` 定义最大文件大小和扩展名/MIME 白名单。
- `validate_upload()` 校验文件名、大小、扩展名、MIME、空内容和可选 SHA-256 checksum。
- 上传被拒绝时抛 `UPLOAD_REJECTED`，details 中包含稳定 `reason`，例如 `file_too_large`、`extension_not_allowed`、`content_type_not_allowed`、`checksum_mismatch`。
- `FileService.upload_bytes()` 已接入默认上传安全策略；业务可以注入更严格的 `upload_policy`。
- `SecurityHeadersConfig` 和 `security_headers()` 提供 CSP、HSTS、X-Frame-Options、Referrer-Policy、Permissions-Policy 等响应头。

第一版还没有实现 secret provider、CORS/Trusted Host runtime middleware 和请求体大小 middleware。这些后续应继续放在 `core.security`，不要散落在业务 app。
