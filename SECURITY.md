# 安全说明

## 密钥与仓库

- 使用 `mcp_server/notification_config.example.json` 与 `api/auto_trader_config.example.json` 复制为本地文件后填写，**勿将含真实密钥的 JSON 推送到远程**。
- 根目录 `.gitignore` 已排除常见敏感路径；若曾误提交，请轮换密钥并考虑清理 Git 历史。

## 网络暴露

- 默认文档中的 `0.0.0.0` 便于局域网调试；面向公网时请改为本机监听 + 反向代理（HTTPS、IP 限制或 VPN）。
- 生产环境建议收紧 FastAPI CORS 的 `allow_origins`。

## 报告漏洞

如你发现与本项目相关的安全问题，请通过私密渠道联系维护者（勿在公开 issue 中粘贴密钥或账户信息）。
