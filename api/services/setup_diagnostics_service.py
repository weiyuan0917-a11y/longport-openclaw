from __future__ import annotations

import os
from typing import Any


def build_longport_diagnostics_response(
    *,
    connection_limit: int,
    active_connections: int,
    usage_pct: float,
    quote_ready: bool,
    trade_ready: bool,
    last_error: str | None,
    last_init_at: str | None,
    probe_requested: bool,
    probe_ok: bool | None,
    probe_error: str | None,
    mcp_pid: int | None,
    feishu_pid: int | None,
    auto_trader_pid: int | None,
    auto_trader_supervisor_pid: int | None,
    mcp_running: bool,
    feishu_running: bool,
    auto_trader_running: bool,
    auto_trader_supervisor_running: bool,
    mcp_pid_file: str,
    feishu_pid_file: str,
    auto_trader_pid_file: str,
    auto_trader_supervisor_pid_file: str,
    auto_trader_supervisor_status_file: str,
    auto_trader_worker_runtime_file: str,
    gateway_enabled: bool,
) -> dict[str, Any]:
    worker_use_api_proxy = str(os.getenv("AUTO_TRADER_WORKER_USE_API_PROXY", "true")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    worker_direct_fallback = str(os.getenv("LONGPORT_DIRECT_FALLBACK", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    mcp_estimated = 2 if mcp_running else 0
    feishu_estimated = 2 if feishu_running else 0
    auto_trader_estimated = (0 if worker_use_api_proxy else 2) if auto_trader_running else 0
    estimated_total = active_connections + mcp_estimated + feishu_estimated + auto_trader_estimated
    estimated_usage_pct_total = round(estimated_total / max(1, int(connection_limit)) * 100, 2)

    if estimated_usage_pct_total >= 90:
        alert_level = "critical"
    elif estimated_usage_pct_total >= 75:
        alert_level = "warning"
    elif estimated_usage_pct_total >= 50:
        alert_level = "notice"
    else:
        alert_level = "ok"

    recommendations: list[str] = []
    if alert_level in {"critical", "warning", "notice"}:
        if feishu_running:
            recommendations.append("如非必要，先停止 Feishu Bot 以立即释放约2个连接。")
        if mcp_running:
            recommendations.append("确保仅保留一个 MCP 主进程，关闭重复实例。")
        if auto_trader_running:
            recommendations.append("自动交易已独立进程运行，若连接吃紧可先停止自动交易。")
            if worker_use_api_proxy and worker_direct_fallback:
                recommendations.append("建议将 LONGPORT_DIRECT_FALLBACK 设为 0，避免 worker 在代理异常时回退直连。")
        if active_connections >= 2:
            if gateway_enabled:
                recommendations.append("Gateway 已启用但 API 仍持有上下文，可检查网关可用性或超时配置。")
            else:
                recommendations.append("API 已持有完整上下文，建议配置 LONGPORT_GATEWAY_BASE_URL 走单入口网关。")
        recommendations.append("将 OPENCLAW_MCP_MAX_LEVEL 保持在 L2，避免误触发高频实盘工具。")
    if not recommendations:
        recommendations.append("连接占用健康，可继续保持单API+单MCP运行模式。")

    return {
        "connection_limit": connection_limit,
        "active_connections_api_process": active_connections,
        "usage_pct_api_process": usage_pct,
        "estimated_connections_total": estimated_total,
        "estimated_usage_pct_total": estimated_usage_pct_total,
        "estimated_breakdown": {
            "api_active": active_connections,
            "mcp_estimated": mcp_estimated,
            "feishu_estimated": feishu_estimated,
            "auto_trader_estimated": auto_trader_estimated,
            "auto_trader_use_api_proxy": worker_use_api_proxy,
            "auto_trader_direct_fallback": worker_direct_fallback,
            "api_gateway_enabled": gateway_enabled,
        },
        "processes": {
            "api": {"pid": os.getpid(), "running": True},
            "mcp": {"pid": mcp_pid, "running": mcp_running, "pid_file": mcp_pid_file},
            "feishu_bot": {"pid": feishu_pid, "running": feishu_running, "pid_file": feishu_pid_file},
            "auto_trader": {"pid": auto_trader_pid, "running": auto_trader_running, "pid_file": auto_trader_pid_file},
            "auto_trader_supervisor": {
                "pid": auto_trader_supervisor_pid,
                "running": auto_trader_supervisor_running,
                "pid_file": auto_trader_supervisor_pid_file,
                "status_file": auto_trader_supervisor_status_file,
                "worker_runtime_file": auto_trader_worker_runtime_file,
            },
        },
        "quote_ctx_ready": quote_ready,
        "trade_ctx_ready": trade_ready,
        "last_init_at": last_init_at,
        "last_error": last_error,
        "probe": {"requested": probe_requested, "ok": probe_ok, "error": probe_error},
        "alert_level": alert_level,
        "recommendations": recommendations,
        "note": "总览为估算值（API为实测，MCP/Feishu按每进程最多2连接估算）。",
    }

