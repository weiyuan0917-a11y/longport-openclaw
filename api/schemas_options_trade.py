from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SubmitOrderBody(BaseModel):
    action: Literal["buy", "sell"]
    symbol: str
    quantity: int = Field(ge=1)
    price: Optional[float] = Field(default=None, gt=0)


class OptionLegBody(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    contracts: int = Field(ge=1)
    price: Optional[float] = Field(default=None, ge=0)


class OptionOrderBody(BaseModel):
    symbol: Optional[str] = None
    side: Optional[Literal["buy", "sell"]] = None
    contracts: Optional[int] = Field(default=None, ge=1)
    price: Optional[float] = Field(default=None, ge=0)
    legs: Optional[list[OptionLegBody]] = None
    max_loss_threshold: Optional[float] = Field(default=None, gt=0)
    max_capital_usage: Optional[float] = Field(default=None, gt=0)
    confirmation_token: Optional[str] = None


class OptionBacktestBody(BaseModel):
    symbol: str
    template: Literal["bull_call_spread", "bear_put_spread", "straddle", "strangle"]
    days: int = Field(default=180, ge=30, le=1500)
    holding_days: int = Field(default=20, ge=3, le=120)
    contracts: int = Field(default=1, ge=1, le=50)
    width_pct: float = Field(default=0.05, ge=0.01, le=0.3)

