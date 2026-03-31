# 启动脚本（小生产 / 开发）

仓库根目录下执行（Windows 用 PowerShell）：

| 场景 | API | 前端 |
|------|-----|------|
| **小生产（默认）** | `.\scripts\start-api.ps1` 或 `./scripts/start-api.sh` | `.\scripts\start-frontend.ps1` 或 `./scripts/start-frontend.sh` |
| **本地开发** | `.\scripts\start-api.ps1 -Dev` 或 `./scripts/start-api.sh --dev` | 同上 |

- **单一来源**：`api.main:app`、各环境绑定地址由仓库根目录 **`backend_uvicorn_spec.py`** 定义；`launcher.py` 与 **`scripts/run_api.py`** 均通过 `build_uvicorn_argv()` 拼装参数，避免多处硬编码。
- 也可直接：`python scripts/run_api.py`（小生产）、`python scripts/run_api.py --dev`、`python scripts/run_api.py --host 0.0.0.0 --port 8000`。
- API 默认绑定 **127.0.0.1:8000**（小生产）；开发模式为 **0.0.0.0:8000** 且带 **`--reload`**。
- 日志目录：`logs/`，按启动时间生成 `api-*.log` / `frontend-*.log`（`logs/*.log` 已加入 `.gitignore`）。
- 自定义端口（仅 shell 示例）：`PORT=8080 ./scripts/start-api.sh`。
- PowerShell 自定义绑定：`.\scripts\start-api.ps1 -BindHost 0.0.0.0`（仍无 reload，适合局域网访问小生产）。

飞书机器人等独立进程若手动启动，可自行重定向，例如：

`python -u mcp_server/feishu_command_bot.py 2>&1 | Tee-Object -FilePath logs\feishu-bot.log -Append`
