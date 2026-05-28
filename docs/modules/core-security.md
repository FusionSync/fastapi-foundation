# Core Security

## Progress

- Status: `connected`
- Done: security headers、trusted hosts、body size limit、secret provider、HTTP client credential secret 注入、password hashing、upload guard、app route security policy、route permission conformance 诊断、权限拒绝审计接入和 private/cloud CSP、cookie、TLS/header hardening 清单已落地。
- Next: _none_

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
- 文件上传必须先经过权限授权，再经过 Security 校验后才能进入 Storage。
- 日志、审计、异常 details 必须先脱敏。
- 生产环境启动时必须执行安全配置检查。

## 当前实现

已落地密码哈希、敏感字段脱敏、上传安全校验、安全响应头和 runtime 安全中间件：

- `PasswordHasher` 使用 PBKDF2-SHA256，默认随机 salt，并提供常量时间校验。
- `redact_sensitive_data()` 递归脱敏 password、token、secret、authorization、cookie 等敏感字段。
- `UploadSecurityPolicy` 定义最大文件大小和扩展名/MIME 白名单。
- `validate_upload()` 校验文件名、大小、扩展名、MIME、空内容和可选 SHA-256 checksum。
- 上传被拒绝时抛 `UPLOAD_REJECTED`，details 中包含稳定 `reason`，例如 `file_too_large`、`extension_not_allowed`、`content_type_not_allowed`、`checksum_mismatch`。
- `FileService.upload_bytes()` 默认拒绝缺少 `AuthorizationService` 的调用，并在通过 `file.upload` 授权后接入默认上传安全策略；业务可以注入更严格的 `upload_policy`。
- `SecurityHeadersConfig` 和 `security_headers()` 提供 CSP、HSTS、X-Frame-Options、Referrer-Policy、Permissions-Policy 等响应头。
- `security_hardening_checklist(profile)` 为 local/private/cloud 输出结构化 hardening 清单；private/cloud 覆盖 CSP、cookie、TLS/HSTS 和通用响应头控制项，private 要求 `Strict-Transport-Security` + `includeSubDomains`，cloud 额外要求 `preload`。
- private/cloud profile template 与派生部署产物会携带同一份 `security_hardening` 清单，发布包必须逐项核对 ingress/reverse proxy 的 CSP、Secure/HttpOnly/SameSite cookie、TLS/HSTS 和安全响应头配置。
- `SecurityHeadersMiddleware` 在 app factory 中为响应补充安全响应头。
- `TrustedHostGuardMiddleware` 按 `settings.security.trusted_hosts` 拒绝不可信 Host，并返回统一 envelope。
- `RequestBodySizeLimitMiddleware` 按 `settings.security.max_request_body_bytes` 拒绝超大请求体，并返回 `REQUEST_TOO_LARGE`。
- CORS runtime 使用 FastAPI/Starlette `CORSMiddleware`，由 `settings.security.cors_origins` 启用。
- `SecretProvider` 协议、`EnvSecretProvider`、`MappingSecretProvider` 和 `resolve_settings_secrets()` 支持通过 `jwt_secret_ref` 从外部 provider 注入 JWT secret；`CoreHttpClient` 也复用同一协议按 `HttpClientCredentialSpec.secret_ref` 注入外部服务 credential header。
- route-level security policy 的 `permissions=["resource:action"]` 会在 app conformance 中校验格式，并要求对应 `PermissionSpec` 已在 `AppModule.permissions` 声明。
- `DatabaseRequestSecurityPipeline` 可注入 `audit_factory`；路由权限被 `AuthorizationService.require()` 拒绝时会写入并提交 `authorization.denied` 审计。

第一版 secret provider 负责启动期 JWT secret 和运行期外部 HTTP credential 解析，不实现远程 Vault 客户端。后续 Kubernetes Secret、Vault 或云 KMS adapter 必须继续实现 `SecretProvider` 协议，不要让业务 app 直接读取外部密钥系统。
