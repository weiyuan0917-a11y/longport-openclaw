from __future__ import annotations

from typing import Any, Optional

from runtime_process_utils import is_pid_alive, managed_subprocess_status


def build_setup_services_status(
    *,
    managed_processes: dict[str, Any],
    feishu_pid_file: str,
    auto_trader_supervisor_pid_file: str,
    auto_runtime: dict[str, Any],
) -> dict[str, Any]:
    """
    Build `/setup/services/status` response payload from managed-process handles and worker runtime.
    Keep response schema identical to the existing API contract.
    """
    feishu_running, feishu_tracking, feishu_pid_out = managed_subprocess_status(
        managed_processes.get("feishu_bot"), feishu_pid_file
    )
    sup_running, sup_tracking, sup_pid_out = managed_subprocess_status(
        managed_processes.get("auto_trader_supervisor"), auto_trader_supervisor_pid_file
    )

    wr = bool(auto_runtime.get("worker_running"))
    wp_int = auto_runtime.get("worker_pid")
    if not isinstance(wp_int, int):
        wp_int = None
    wp_alive = is_pid_alive(wp_int) if wp_int else False
    worker_tracking = "pid_file" if wp_alive else ("runtime" if wr else "none")
    worker_pid_out: Optional[int] = wp_int if wr or wp_alive else None

    return {
        "feishu_bot_running": feishu_running,
        "feishu_bot_tracking": feishu_tracking,
        "feishu_bot_pid": feishu_pid_out,
        "auto_trader_scheduler_running": bool(auto_runtime.get("worker_running")),
        "auto_trader_supervisor_running": sup_running,
        "auto_trader_supervisor_tracking": sup_tracking,
        "auto_trader_supervisor_pid": sup_pid_out,
        "auto_trader_worker_tracking": worker_tracking,
        "auto_trader_worker_pid": worker_pid_out,
        "auto_trader_runtime": auto_runtime,
    }

