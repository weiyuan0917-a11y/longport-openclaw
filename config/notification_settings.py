from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from config.env_loader import load_project_env

_ROOT = Path(__file__).resolve().parents[1]
load_project_env(_ROOT)


def _read_notification_file(notification_config_path: str) -> dict[str, Any]:
    if not notification_config_path or not os.path.exists(notification_config_path):
        return {}
    try:
        with open(notification_config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_feishu_app_config(notification_config_path: str | None = None) -> dict[str, str]:
    """
    Resolve Feishu app config with env override.
    Priority: environment variables > notification_config.json.
    """
    file_cfg: dict[str, Any] = {}
    if notification_config_path:
        file_cfg = _read_notification_file(notification_config_path).get("feishu_app", {}) or {}

    app_id = os.getenv("FEISHU_APP_ID", "").strip() or str(file_cfg.get("app_id", "")).strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip() or str(file_cfg.get("app_secret", "")).strip()
    scheduled_chat_id = os.getenv("FEISHU_SCHEDULED_CHAT_ID", "").strip() or str(file_cfg.get("scheduled_chat_id", "")).strip()

    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "scheduled_chat_id": scheduled_chat_id,
    }
