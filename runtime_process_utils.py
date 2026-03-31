"""
跨 launcher / api.main 共用的进程与 pid 文件工具（避免重复实现）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Literal, Optional

Tracking = Literal["subprocess", "pid_file", "none"]


def _win_subprocess_no_window_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def read_pid_file(path: str | os.PathLike[str]) -> Optional[int]:
    p = Path(path)
    try:
        if not p.exists():
            return None
        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        pid = int(raw)
        return pid if pid > 0 else None
    except Exception:
        return None


def is_pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            r = subprocess.run(  # noqa: S603
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
                **_win_subprocess_no_window_kwargs(),
            )
            out = (r.stdout or "").strip().lower()
            if not out:
                return False
            if "no tasks are running" in out or "没有运行的任务" in out:
                return False
            return str(int(pid)) in out
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def managed_subprocess_status(
    proc: Optional[subprocess.Popen[Any]],
    pid_file: str | os.PathLike[str],
) -> tuple[bool, Tracking, Optional[int]]:
    """
    子进程句柄优先，否则用 pid 文件判断存活（与 Setup status 飞书/Supervisor 语义一致）。
    返回 (是否运行中, tracking, 对外展示的 pid)。
    """
    via_sub = bool(proc is not None and proc.poll() is None)
    pid_from_file = read_pid_file(pid_file)
    via_pid = bool(pid_from_file and is_pid_alive(pid_from_file))
    running = via_sub or via_pid
    tracking: Tracking = "subprocess" if via_sub else ("pid_file" if via_pid else "none")
    pid_out: Optional[int] = None
    if running:
        if via_sub and proc is not None:
            pid_out = int(proc.pid)
        else:
            pid_out = pid_from_file
    return running, tracking, pid_out
