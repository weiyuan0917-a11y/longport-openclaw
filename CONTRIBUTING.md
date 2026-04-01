# Contributing Guide

感谢你对 LongPort OpenClaw 的关注与贡献！

## 开始之前

- 请先阅读 `README.md`、`SECURITY.md`、`THIRD_PARTY_NOTICES.md`。
- 严禁提交任何密钥、Token、Webhook、账户凭证。
- 提交前请确保本地可运行并完成基本验证。

## 开发环境

- Python 3.10+
- Node.js 18+

安装依赖：

```bash
pip install -r requirements.txt
cd frontend
npm install
```

## 分支与提交规范

建议流程：

1. Fork 本仓库
2. 新建分支：`feat/xxx`、`fix/xxx`、`docs/xxx`
3. 提交信息建议：
   - `feat: ...`
   - `fix: ...`
   - `docs: ...`
   - `refactor: ...`
4. 发起 PR 到 `main`

## 代码与文件规范

- 保持改动聚焦（一个 PR 只解决一个主题）
- 不提交构建产物、运行缓存、日志、私有配置
- 文档变更尽量与代码变更同步

## 验证建议

PR 前至少完成：

- 后端可启动
- 前端可启动
- 核心页面可访问（Dashboard / Market / Backtest / AutoTrader）

建议在 PR 描述中包含：

- 变更背景与目标
- 主要改动点（按模块）
- 验证步骤与结果
- 风险与回滚方案

## 安全与合规

- 安全问题请优先私下联系维护者（见 `SECURITY.md`）
- 涉及第三方数据/券商接口的改动，请遵守对应服务条款

