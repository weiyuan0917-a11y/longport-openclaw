#!/usr/bin/env python3
"""
从仓库根启动 API：参数与 backend_uvicorn_spec 一致。
也可仅打印 uvicorn 参数 JSON（供 PowerShell Start-Process 等消费）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ensure_repo_env() -> None:
    os.chdir(ROOT)
    prev = os.environ.get("PYTHONPATH", "")
    root_s = str(ROOT)
    if not prev:
        os.environ["PYTHONPATH"] = root_s
    elif root_s not in prev.split(os.pathsep):
        os.environ["PYTHONPATH"] = root_s + os.pathsep + prev


def main() -> None:
    _ensure_repo_env()
    sys.path.insert(0, str(ROOT))

    from backend_uvicorn_spec import (
        DEFAULT_API_PORT,
        SCRIPT_DEV_HOST,
        SCRIPT_SMALL_PROD_HOST,
        build_uvicorn_argv,
    )

    p = argparse.ArgumentParser(description="Start LongPort API (uvicorn) using backend_uvicorn_spec.")
    p.add_argument("--dev", action="store_true", help="bind dev host + --reload")
    p.add_argument("--small-prod", action="store_true", help="bind small-prod host, no reload (default)")
    p.add_argument("--host", default="", help="override --host")
    p.add_argument("--port", type=int, default=0, help=f"listen port (default {DEFAULT_API_PORT})")
    p.add_argument(
        "--print-argv-json",
        action="store_true",
        help="print JSON list of argv after python (for scripts); do not start server",
    )
    args = p.parse_args()

    port = int(args.port or DEFAULT_API_PORT)
    if args.host:
        host = str(args.host)
    elif args.dev:
        host = SCRIPT_DEV_HOST
    else:
        host = SCRIPT_SMALL_PROD_HOST
    reload = bool(args.dev)
    argv_tail = build_uvicorn_argv(host, port, reload=reload)

    if args.print_argv_json:
        print(json.dumps(argv_tail))
        return

    os.execv(sys.executable, [sys.executable, *argv_tail])


if __name__ == "__main__":
    main()
