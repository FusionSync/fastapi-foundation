# FastAPI Foundation Public Documents

本目录用于生成对外可见的文档站点内容（可直接作为 GitHub Pages 文档源）。

## 目录结构

- `index.md`：对外站点首页
- `guides/`：面向开发者的操作指南
- `api/`：对外 API/接口与运行能力说明

## 使用方式

1. 将 `documents/` 作为独立文档站点源目录。
2. 通过你的 GitHub Actions（或其他 CI）把该目录发布到 GitHub Pages。
3. 对外文档内容优先放在这里，内部实现细节保持在 `docs/`。
