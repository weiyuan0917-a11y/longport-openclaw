"""
后端 uvicorn 启动参数单一来源（launcher、脚本、silent-run 等应由此拼装命令）。
"""
from __future__ import annotations

# 应用入口（勿在多处硬编码字符串）
UVICORN_APP = "api.main:app"

# 默认端口
DEFAULT_API_PORT = 8000

# 启动器历史行为：始终绑定 0.0.0.0（健康检查走 127.0.0.1）
LAUNCHER_UVICORN_HOST = "0.0.0.0"

# 脚本「小生产」默认绑定
SCRIPT_SMALL_PROD_HOST = "127.0.0.1"

# 开发模式绑定（与脚本 -Dev / --dev 一致）
SCRIPT_DEV_HOST = "0.0.0.0"


def build_uvicorn_argv(host: str, port: int, *, reload: bool = False) -> list[str]:
    """供 python -m 之前的参数列表：["-m", "uvicorn", ...]"""
    argv = [
        "-m",
        "uvicorn",
        UVICORN_APP,
        "--host",
        str(host),
        "--port",
        str(int(port)),
    ]
    if reload:
        argv.append("--reload")
    return argv
