from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SetupConfigBody(BaseModel):
    longport_app_key: Optional[str] = None
    longport_app_secret: Optional[str] = None
    longport_access_token: Optional[str] = None
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_scheduled_chat_id: Optional[str] = None
    finnhub_api_key: Optional[str] = None
    tiingo_api_key: Optional[str] = None
    fred_api_key: Optional[str] = None
    coingecko_api_key: Optional[str] = None
    openclaw_mcp_max_level: Optional[str] = None
    openclaw_mcp_allow_l3: Optional[str] = None
    openclaw_mcp_l3_confirmation_token: Optional[str] = None
    openbb_enabled: Optional[str] = None
    openbb_base_url: Optional[str] = None
    openbb_timeout_seconds: Optional[str] = None


class SetupStartBody(BaseModel):
    start_feishu_bot: bool = True
    enable_auto_trader: bool = False


class SetupRiskConfigBody(BaseModel):
    max_order_amount: Optional[float] = Field(default=None, gt=0)
    max_daily_loss_pct: Optional[float] = Field(default=None, ge=0, le=1)
    stop_loss_pct: Optional[float] = Field(default=None, ge=0, le=1)
    max_position_pct: Optional[float] = Field(default=None, ge=0, le=1)
    enabled: Optional[bool] = None


class SetupStopBody(BaseModel):
    stop_feishu_bot: bool = False
    stop_auto_trader: bool = False


class SetupStopAllBody(BaseModel):
    stop_backend: bool = True
    stop_frontend: bool = True
    stop_feishu_bot: bool = True
    stop_auto_trader: bool = True

