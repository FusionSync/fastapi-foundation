# 快速开始

This guide is for developers who have never used this framework before.

## 1. 理解目录约定
- 每个 APP 模块通常放在 `apps/<app_name>/`
- 关键入口一般是 `main.py` / `router.py` / `schemas.py` / `service.py`（具体以该 APP 内约定为准）
- 配置和依赖通过统一启动入口注入

## 2. 新建你的第一个 APP 模块
1. 在 `apps/` 下创建新文件夹，如 `apps/my_app`
2. 参考现有模块实现 `router` 和 `service`
3. 在统一注册点（如 `app/main.py`）挂载模块路由
4. 在 `config` 中加入模块相关配置（如有需要）
5. 写一个冒烟接口并验证启动

## 3. 测试与发布
- 先跑本地自测
- 补充最小 API 示例
- 更新本目录相应文档并提交
