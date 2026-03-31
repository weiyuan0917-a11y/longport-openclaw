from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException
from pydantic import ValidationError


def apply_auto_trader_config_update(
    *,
    payload: dict[str, Any],
    update_config: Callable[[dict[str, Any]], dict[str, Any]],
    sync_worker: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    patch = dict(payload or {})
    pair_mode_execution_forced_off = False
    pair_mode_enabled = bool(patch.get("pair_mode"))
    auto_execute_enabled = bool(patch.get("auto_execute"))
    pair_mode_allow_auto_execute = bool(patch.get("pair_mode_allow_auto_execute"))
    if pair_mode_enabled and auto_execute_enabled and not pair_mode_allow_auto_execute:
        patch["auto_execute"] = False
        pair_mode_execution_forced_off = True
    cfg = update_config(patch)
    worker_sync = sync_worker(cfg)
    return {
        "ok": True,
        "config": cfg,
        "pair_mode_execution_forced_off": pair_mode_execution_forced_off,
        "message": "ETF配对模式下已自动关闭实盘执行（可在高级开关中手动放开）" if pair_mode_execution_forced_off else "",
        **worker_sync,
    }


def build_auto_trader_status_response(
    *,
    status: dict[str, Any],
    runtime: dict[str, Any],
    research: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    out = dict(status or {})
    out["running"] = bool(runtime.get("worker_running"))
    out["execution_host"] = "worker_process"
    out["config"] = config
    out["last_scan_summary"] = runtime.get("last_scan_summary")
    worker_summary = runtime.get("last_scan_summary")
    if isinstance(worker_summary, dict):
        st = worker_summary.get("scan_time")
        if st:
            out["last_scan_at"] = st
        # 扫描与计数在独立 Worker 进程中执行；API 进程内 auto_trader 未跑调度，这两项常为 0。
        # 用最近一次落盘的扫描摘要覆盖，页面才能显示真实「今日交易 / 连续无信号」。
        if "daily_trade_count" in worker_summary:
            out["daily_trade_count"] = worker_summary.get("daily_trade_count")
        if "consecutive_no_signal_rounds" in worker_summary:
            out["consecutive_no_signal_rounds"] = worker_summary.get("consecutive_no_signal_rounds")
    worker_blob = runtime.get("worker")
    if isinstance(worker_blob, dict) and not out.get("last_scan_at"):
        manual_at = worker_blob.get("last_manual_scan_at")
        if manual_at:
            out["last_scan_at"] = manual_at
    worker_status = runtime.get("worker_status")
    if isinstance(worker_status, dict):
        # Worker 进程内维护的连亏计数/最近估算盈亏才是实时值；API 进程内实例可能长期不变。
        for key in (
            "consecutive_loss_stop_enabled",
            "consecutive_loss_stop_count",
            "consecutive_loss_count",
            "consecutive_loss_stop_triggered",
            "consecutive_loss_stop_reason",
            "consecutive_loss_stop_at",
            "last_trade_pnl_estimate",
        ):
            if key in worker_status:
                out[key] = worker_status.get(key)
    out["runtime"] = runtime
    out["research"] = research
    wl = runtime.get("research_allocation_last")
    if wl is not None:
        ra = out.get("research_allocation")
        if not isinstance(ra, dict):
            ra = {}
        ra = dict(ra)
        ra["worker_last"] = wl
        out["research_allocation"] = ra
    return out


def build_auto_trader_config_policy(
    *,
    locked_fields: set[str],
    field_rules: dict[str, Any],
) -> dict[str, Any]:
    return {
        "mode": "agent_safe_update",
        "locked_fields": sorted(list(locked_fields)),
        "rules": field_rules,
        "notes": [
            "Use POST /auto-trader/config/agent for OpenClaw-style automated tuning.",
            "Account-level hard risk fields are locked and cannot be changed by agent.",
            "Out-of-range values are rejected with policy_violation details.",
        ],
    }


def apply_agent_policy_update(
    *,
    raw_payload: dict[str, Any],
    current_config: dict[str, Any],
    validate_update: Callable[[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], list[dict[str, Any]]]],
    locked_fields: set[str],
    allowed_field_rules: dict[str, Any],
    update_config: Callable[[dict[str, Any]], dict[str, Any]],
    sync_worker: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    payload, violations = validate_update(raw_payload, current_config)
    if violations:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "policy_violation",
                "violations": violations,
                "locked_fields": sorted(list(locked_fields)),
                "allowed_fields": sorted(list(allowed_field_rules.keys())),
            },
        )
    cfg = update_config(payload)
    worker_sync = sync_worker(cfg)
    return {"ok": True, "config": cfg, "applied_fields": sorted(list(payload.keys())), **worker_sync}


def apply_template_with_sync(
    *,
    template_name: str,
    apply_template: Callable[[str], dict[str, Any]],
    sync_worker: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    try:
        cfg = apply_template(template_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    worker_sync = sync_worker(cfg)
    return {"ok": True, "config": cfg, "template": template_name, **worker_sync}


def preview_template_safe(*, template_name: str, preview_template: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    try:
        return preview_template(template_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def import_config_with_rollback(
    *,
    config_obj: dict[str, Any],
    current_config: dict[str, Any],
    validate_import_config: Callable[[dict[str, Any]], dict[str, Any]],
    update_config: Callable[[dict[str, Any]], dict[str, Any]],
    sync_worker: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    try:
        payload = validate_import_config(config_obj)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail={"error": "invalid_config_schema", "details": e.errors()}) from e
    try:
        cfg = update_config(payload)
    except Exception as e:
        update_config(current_config)
        raise HTTPException(status_code=500, detail=f"import_failed_rollback_applied: {e}") from e
    worker_sync = sync_worker(cfg)
    return {"ok": True, "config": cfg, "validated": True, **worker_sync}


def rollback_config_with_sync(
    *,
    backup_id: str,
    rollback_config: Callable[[str], dict[str, Any]],
    sync_worker: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    try:
        cfg = rollback_config(backup_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    worker_sync = sync_worker(cfg)
    return {"ok": True, "config": cfg, "rollback_to": backup_id, **worker_sync}


def preview_rollback_safe(*, backup_id: str, preview_rollback: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    try:
        return preview_rollback(backup_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

