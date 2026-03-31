"""
通知偏好：读写 mcp_server/notification_config.json 中的 notification_preferences 节点。
供 API、AutoTrader、飞书机器人等读取开关。
"""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any

_LOCK = threading.RLock()

# 与 frontend 信号中心 localStorage key 一致，便于文档说明
SIGNALS_PAGE_STORAGE_KEY = "signals_page_symbols_v1"
FEISHU_BUILTIN_REVERSAL_CONDITION_IDS = [
    "rsi_rebound",
    "macd_bullish_cross_below_zero",
    "bollinger_rebound",
    "hammer_candle",
    "volume_rebound",
    "ma5_cross_ma20",
]

DEFAULT_NOTIFICATION_PREFERENCES: dict[str, Any] = {
    "scheduled_market_report": {
        "enabled": True,
        "note": "由飞书指令机器人进程在配置 scheduled_chat_id 时执行整点推送；关闭后机器人侧跳过发送。",
    },
    "semi_auto_pending_signal": {
        "enabled": True,
        "note": "半自动：生成待确认信号时推送飞书。",
    },
    "full_auto_execution": {
        "enabled": True,
        "notify_on_failure": True,
        "note": "全自动：成交成功/失败时推送（失败可单独关闭）。",
    },
    "observer_mode_digest": {
        "enabled": True,
        "note": "观察模式：连续无信号达到阈值时的汇总推送。",
    },
    "bottom_reversal_watch": {
        "enabled": False,
        "symbols": [],
        "poll_interval_seconds": 300,
        "only_on_edge": True,
        "cooldown_minutes": 120,
        "note": "与信号中心同源：bottom_reversal_hint；由 API 进程后台轮询并推送飞书。",
    },
    "feishu_builtin_reversal_monitor": {
        "enabled": False,
        "selection_mode": "multi",  # multi | single
        "selected_conditions": FEISHU_BUILTIN_REVERSAL_CONDITION_IDS,
        "note": "飞书机器人内置的多条件反转检测线程（与信号中心算法不同）。默认关闭，避免与 API 侧重复。",
    },
}


def notification_config_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "mcp_server", "notification_config.json")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read_config_file() -> dict[str, Any]:
    path = notification_config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_preferences(raw: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_merge(DEFAULT_NOTIFICATION_PREFERENCES, raw)
    br = merged.get("bottom_reversal_watch")
    if isinstance(br, dict):
        syms = br.get("symbols")
        if not isinstance(syms, list):
            syms = []
        clean = [str(s).strip().upper() for s in syms if str(s).strip()][:30]
        br["symbols"] = clean
        br["poll_interval_seconds"] = max(60, min(86400, int(br.get("poll_interval_seconds") or 300)))
        br["cooldown_minutes"] = max(0, min(10080, int(br.get("cooldown_minutes") or 120)))
        br["only_on_edge"] = bool(br.get("only_on_edge", True))
        br["enabled"] = bool(br.get("enabled", False))
    fb = merged.get("feishu_builtin_reversal_monitor")
    if isinstance(fb, dict):
        fb["enabled"] = bool(fb.get("enabled", False))
        mode = str(fb.get("selection_mode", "multi")).strip().lower()
        if mode not in {"multi", "single"}:
            mode = "multi"
        fb["selection_mode"] = mode
        selected = fb.get("selected_conditions")
        if not isinstance(selected, list):
            selected = []
        valid = set(FEISHU_BUILTIN_REVERSAL_CONDITION_IDS)
        clean: list[str] = []
        for item in selected:
            cid = str(item).strip()
            if cid and cid in valid and cid not in clean:
                clean.append(cid)
        if not clean:
            clean = list(FEISHU_BUILTIN_REVERSAL_CONDITION_IDS)
        if mode == "single":
            clean = clean[:1]
        fb["selected_conditions"] = clean
    return merged


def load_notification_preferences() -> dict[str, Any]:
    with _LOCK:
        data = _read_config_file()
        raw = data.get("notification_preferences")
        raw_dict = raw if isinstance(raw, dict) else {}
        return _normalize_preferences(raw_dict)


def save_notification_preferences(prefs: dict[str, Any]) -> dict[str, Any]:
    """合并写入 notification_config.json，仅替换 notification_preferences 键。"""
    normalized = _normalize_preferences(prefs if isinstance(prefs, dict) else {})
    path = notification_config_path()
    with _LOCK:
        data = _read_config_file()
        data["notification_preferences"] = normalized
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    return normalized


def merge_patch_notification_preferences(patch: dict[str, Any]) -> dict[str, Any]:
    """与当前已保存偏好深度合并后校验、落盘。"""
    cur = load_notification_preferences()
    merged = _deep_merge(cur, patch if isinstance(patch, dict) else {})
    return save_notification_preferences(merged)


def should_send_semi_auto_pending() -> bool:
    p = load_notification_preferences().get("semi_auto_pending_signal")
    return bool(isinstance(p, dict) and p.get("enabled", True))


def should_send_full_auto_execution(*, success: bool) -> bool:
    p = load_notification_preferences().get("full_auto_execution")
    if not isinstance(p, dict) or not p.get("enabled", True):
        return False
    if success:
        return True
    return bool(p.get("notify_on_failure", True))


def should_send_observer_digest() -> bool:
    p = load_notification_preferences().get("observer_mode_digest")
    return bool(isinstance(p, dict) and p.get("enabled", True))


def should_run_scheduled_market_report() -> bool:
    p = load_notification_preferences().get("scheduled_market_report")
    return bool(isinstance(p, dict) and p.get("enabled", True))


def should_run_feishu_builtin_reversal() -> bool:
    p = load_notification_preferences().get("feishu_builtin_reversal_monitor")
    return bool(isinstance(p, dict) and p.get("enabled", False))
