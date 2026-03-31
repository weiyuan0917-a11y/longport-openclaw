import json
import os
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from config.notification_settings import resolve_feishu_app_config
from api.notification_preferences import (
    DEFAULT_NOTIFICATION_PREFERENCES,
    load_notification_preferences,
    merge_patch_notification_preferences,
)

router = APIRouter(tags=["notifications"])

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MCP_DIR = os.path.join(ROOT, "mcp_server")


@router.get("/notifications/status")
def notifications_status() -> dict[str, Any]:
    cfg_path = os.path.join(MCP_DIR, "notification_config.json")
    data: dict[str, Any] = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    feishu_cfg = resolve_feishu_app_config(cfg_path)
    env_override = any(
        bool(os.getenv(k))
        for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_SCHEDULED_CHAT_ID")
    )
    prefs = load_notification_preferences()
    return {
        "feishu_app_configured": bool(feishu_cfg.get("app_id") and feishu_cfg.get("app_secret")),
        "scheduled_chat_id": feishu_cfg.get("scheduled_chat_id", ""),
        "feishu_bots_count": len(data.get("feishu_bots", [])),
        "feishu_config_source": "env" if env_override else "file",
        "preferences_summary": {
            "scheduled_market_report": bool((prefs.get("scheduled_market_report") or {}).get("enabled", True)),
            "semi_auto_pending_signal": bool((prefs.get("semi_auto_pending_signal") or {}).get("enabled", True)),
            "full_auto_execution": bool((prefs.get("full_auto_execution") or {}).get("enabled", True)),
            "observer_mode_digest": bool((prefs.get("observer_mode_digest") or {}).get("enabled", True)),
            "bottom_reversal_watch": bool((prefs.get("bottom_reversal_watch") or {}).get("enabled", False)),
            "feishu_builtin_reversal": bool((prefs.get("feishu_builtin_reversal_monitor") or {}).get("enabled", False)),
        },
    }


@router.get("/notifications/preferences")
def notifications_preferences_get() -> dict[str, Any]:
    return {
        "ok": True,
        "preferences": load_notification_preferences(),
        "defaults": DEFAULT_NOTIFICATION_PREFERENCES,
        "signals_page_storage_key": "signals_page_symbols_v1",
    }


@router.put("/notifications/preferences")
def notifications_preferences_put(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    patch = body.get("preferences") if isinstance(body.get("preferences"), dict) else body
    if not isinstance(patch, dict):
        raise HTTPException(status_code=400, detail="invalid_body_expect_preferences_object")
    merged = merge_patch_notification_preferences(patch)
    return {"ok": True, "preferences": merged}

