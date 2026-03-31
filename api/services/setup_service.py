from __future__ import annotations

import os
from typing import Any, Callable


def build_setup_config_response(
    *,
    env_data: dict[str, Any],
    feishu_cfg: dict[str, Any],
    mask_secret: Callable[[str], str],
) -> dict[str, Any]:
    tiingo_val = env_data.get("TIINGO_API_KEY") or env_data.get("NEWS_API_KEY", "")
    return {
        "configured": {
            "longport": all(env_data.get(k) for k in ("LONGPORT_APP_KEY", "LONGPORT_APP_SECRET", "LONGPORT_ACCESS_TOKEN")),
            "feishu": bool(feishu_cfg.get("app_id") and feishu_cfg.get("app_secret")),
            "market_apis": any(
                env_data.get(k) for k in ("FINNHUB_API_KEY", "TIINGO_API_KEY", "NEWS_API_KEY", "FRED_API_KEY", "COINGECKO_API_KEY")
            ),
            "openbb": str(env_data.get("OPENBB_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"},
        },
        "values": {
            "longport_app_key": mask_secret(env_data.get("LONGPORT_APP_KEY", "")),
            "longport_app_secret": mask_secret(env_data.get("LONGPORT_APP_SECRET", "")),
            "longport_access_token": mask_secret(env_data.get("LONGPORT_ACCESS_TOKEN", "")),
            "feishu_app_id": mask_secret(feishu_cfg.get("app_id", "")),
            "feishu_app_secret": mask_secret(feishu_cfg.get("app_secret", "")),
            "feishu_scheduled_chat_id": feishu_cfg.get("scheduled_chat_id", ""),
            "finnhub_api_key": mask_secret(env_data.get("FINNHUB_API_KEY", "")),
            "tiingo_api_key": mask_secret(tiingo_val),
            "fred_api_key": mask_secret(env_data.get("FRED_API_KEY", "")),
            "coingecko_api_key": mask_secret(env_data.get("COINGECKO_API_KEY", "")),
            "openclaw_mcp_max_level": env_data.get("OPENCLAW_MCP_MAX_LEVEL", "L2"),
            "openclaw_mcp_allow_l3": env_data.get("OPENCLAW_MCP_ALLOW_L3", "false"),
            "openclaw_mcp_l3_confirmation_token": mask_secret(env_data.get("OPENCLAW_MCP_L3_CONFIRMATION_TOKEN", "")),
            "openbb_enabled": env_data.get("OPENBB_ENABLED", "false"),
            "openbb_base_url": env_data.get("OPENBB_BASE_URL", "http://127.0.0.1:6900"),
            "openbb_timeout_seconds": env_data.get("OPENBB_TIMEOUT_SECONDS", "8"),
        },
    }


def apply_setup_env_updates(
    *,
    payload: dict[str, Any],
    env_data: dict[str, str],
    env_var_map: dict[str, str],
) -> list[str]:
    changed: list[str] = []
    for field, env_key in env_var_map.items():
        val = payload.get(field)
        if val is None:
            continue
        clean = str(val).strip()
        if clean == "":
            # Ignore empty updates to avoid accidental secret wipe.
            continue
        env_data[env_key] = clean
        os.environ[env_key] = clean
        changed.append(env_key)
    return changed

