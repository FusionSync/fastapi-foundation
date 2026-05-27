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
