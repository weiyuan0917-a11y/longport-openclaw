from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

BacktestKline = Literal["1m", "5m", "10m", "30m", "1h", "2h", "4h", "1d"]


class BacktestBarItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    date: str
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0


class BacktestCompareBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol: str = "RXRX.US"
    days: int = 180
    periods: int = 0
    kline: BacktestKline = "1d"
    initial_capital: float = 100000.0
    execution_mode: Literal["next_open", "bar_close"] = "next_open"
    slippage_bps: float = 3.0
    commission_bps: float | None = None
    stamp_duty_bps: float | None = None
    walk_forward_windows: int = 1
    ml_filter_enabled: bool = False
    ml_model_type: Literal["logreg", "random_forest", "gbdt"] = "logreg"
    ml_threshold: float = 0.55
    ml_horizon_days: int = 5
    ml_train_ratio: float = 0.7
    include_trades: bool = False
    trade_limit: int = 50
    trade_offset: int = 0
    strategy_key: str | None = None
    include_best_kline: bool = False
    bars: list[BacktestBarItem] | None = None
    strategy_params: dict[str, dict[str, Any]] | None = None
    include_bars_in_response: bool = False
    use_server_kline_cache: bool = False


class BacktestKlineCacheFetchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symbol: str = "AAPL.US"
    periods: int = 180
    days: int = 180
    kline: BacktestKline = "1d"
    force_refresh: bool = False

