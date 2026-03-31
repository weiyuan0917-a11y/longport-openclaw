from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from api.schemas_backtest import BacktestKline


class AutoTraderConfigBody(BaseModel):
    enabled: Optional[bool] = None
    auto_execute: Optional[bool] = None
    pair_mode_allow_auto_execute: Optional[bool] = None
    dry_run_mode: Optional[bool] = None
    active_template: Optional[str] = None
    signal_relaxed_mode: Optional[bool] = None
    auto_prune_invalid_symbols: Optional[bool] = None
    observer_mode_enabled: Optional[bool] = None
    observer_no_signal_rounds: Optional[int] = Field(default=None, ge=1, le=50)
    auto_sell_enabled: Optional[bool] = None
    sell_full_position: Optional[bool] = None
    sell_order_quantity: Optional[int] = Field(default=None, ge=1)
    same_symbol_max_sells_per_day: Optional[int] = Field(default=None, ge=1, le=20)
    same_symbol_cooldown_minutes: Optional[int] = Field(default=None, ge=0, le=240)
    same_symbol_max_trades_per_day: Optional[int] = Field(default=None, ge=1, le=20)
    avoid_add_to_existing_position: Optional[bool] = None
    market: Optional[Literal["us", "hk", "cn"]] = None
    pair_mode: Optional[bool] = None
    interval_seconds: Optional[int] = Field(default=None, ge=30, le=3600)
    top_n: Optional[int] = Field(default=None, ge=1, le=30)
    kline: Optional[BacktestKline] = None
    backtest_days: Optional[int] = Field(default=None, ge=30, le=365)
    signal_bars_days: Optional[int] = Field(default=None, ge=30, le=365)
    order_quantity: Optional[int] = Field(default=None, ge=1)
    entry_rule: Optional[Literal["strategy_cross", "breakout", "mean_reversion"]] = None
    breakout_lookback_bars: Optional[int] = Field(default=None, ge=5, le=240)
    breakout_volume_ratio: Optional[float] = Field(default=None, ge=0, le=10)
    mean_reversion_rsi_threshold: Optional[float] = Field(default=None, ge=1, le=80)
    mean_reversion_deviation_pct: Optional[float] = Field(default=None, ge=0, le=30)
    exit_rules: Optional[list[str]] = None
    rule_priority: Optional[list[str]] = None
    hard_stop_pct: Optional[float] = Field(default=None, ge=0.1, le=50)
    take_profit_pct: Optional[float] = Field(default=None, ge=0.1, le=200)
    time_stop_hours: Optional[int] = Field(default=None, ge=1, le=720)
    sizer: Optional[dict[str, Any]] = None
    cost_model: Optional[dict[str, Any]] = None
    max_daily_trades: Optional[int] = Field(default=None, ge=1, le=100)
    daily_loss_circuit_enabled: Optional[bool] = None
    daily_loss_limit_pct: Optional[float] = Field(default=None, ge=0, le=1)
    consecutive_loss_stop_enabled: Optional[bool] = None
    consecutive_loss_stop_count: Optional[int] = Field(default=None, ge=1, le=20)
    max_position_value: Optional[float] = Field(default=None, gt=0)
    max_total_exposure: Optional[float] = Field(default=None, ge=0, le=1)
    min_cash_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    same_direction_max_new_orders_per_scan: Optional[int] = Field(default=None, ge=1, le=20)
    max_concurrent_long_positions: Optional[int] = Field(default=None, ge=1, le=200)
    ml_filter_enabled: Optional[bool] = None
    ml_model_type: Optional[Literal["logreg", "random_forest", "gbdt"]] = None
    ml_threshold: Optional[float] = Field(default=None, ge=0.5, le=0.95)
    ml_horizon_days: Optional[int] = Field(default=None, ge=1, le=30)
    ml_train_ratio: Optional[float] = Field(default=None, ge=0.5, le=0.9)
    ml_walk_forward_windows: Optional[int] = Field(default=None, ge=1, le=12)
    ml_filter_cache_minutes: Optional[int] = Field(default=None, ge=0, le=1440)
    research_allocation_enabled: Optional[bool] = None
    research_allocation_max_age_minutes: Optional[int] = Field(default=None, ge=0, le=10080)
    research_allocation_snapshot_id: Optional[str] = None
    research_allocation_notional_scale: Optional[float] = Field(default=None, ge=0.01, le=3.0)
    merge_strategy_matrix_top3: Optional[bool] = None
    merge_strategy_matrix_top3_snapshot_id: Optional[str] = None
    strategies: Optional[list[str]] = None
    strategy_params_map: Optional[dict[str, dict[str, Any]]] = None
    universe: Optional[dict[str, list[str]]] = None
    pair_pool: Optional[dict[str, dict[str, str]]] = None


class AutoTraderTemplateApplyBody(BaseModel):
    name: Literal["trend", "mean_reversion", "defensive"]


class AutoTraderImportBody(BaseModel):
    config: dict[str, Any]


class AutoTraderRollbackBody(BaseModel):
    backup_id: str


class AutoTraderConfirmBody(BaseModel):
    price: Optional[float] = Field(default=None, gt=0)


class AutoTraderResearchRunBody(BaseModel):
    market: Optional[Literal["us", "hk", "cn"]] = None
    kline: Optional[BacktestKline] = None
    top_n: Optional[int] = Field(default=None, ge=1, le=30)
    backtest_days: Optional[int] = Field(default=None, ge=90, le=365)
    async_run: Optional[bool] = False


class AutoTraderStrategyMatrixRunBody(BaseModel):
    market: Optional[Literal["us", "hk", "cn"]] = None
    top_n: Optional[int] = Field(default=None, ge=1, le=30)
    max_strategies: Optional[int] = Field(default=None, ge=1, le=20)
    max_drawdown_limit_pct: Optional[float] = Field(default=None, ge=1, le=80)
    min_symbols_used: Optional[int] = Field(default=None, ge=1, le=30)
    matrix_overrides: Optional[dict[str, Any]] = None
    async_run: Optional[bool] = True


class AutoTraderMlMatrixRunBody(BaseModel):
    market: Optional[Literal["us", "hk", "cn"]] = None
    kline: Optional[BacktestKline] = None
    top_n: Optional[int] = Field(default=None, ge=1, le=30)
    signal_bars_days: Optional[int] = Field(default=None, ge=120, le=365)
    matrix_overrides: Optional[dict[str, Any]] = None
    constraints: Optional[dict[str, Any]] = None
    ranking_weights: Optional[dict[str, Any]] = None
    async_run: Optional[bool] = True


class AutoTraderMlMatrixApplyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    variant: Literal["auto", "balanced", "high_precision", "high_coverage", "best_score"] = "auto"
    enable_ml_filter: bool = True
    snapshot_id: Optional[str] = None


class SizerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["fixed", "risk_percent", "volatility"]
    quantity: Optional[int] = Field(default=None, ge=1)
    risk_pct: Optional[float] = Field(default=None, ge=0, le=1)
    target_vol_pct: Optional[float] = Field(default=None, ge=0, le=1)


class CostModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commission_bps: Optional[float] = Field(default=None, ge=0, le=500)
    slippage_bps: Optional[float] = Field(default=None, ge=0, le=500)


class AutoTraderImportConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    auto_execute: Optional[bool] = None
    pair_mode_allow_auto_execute: Optional[bool] = None
    dry_run_mode: Optional[bool] = None
    signal_relaxed_mode: Optional[bool] = None
    auto_prune_invalid_symbols: Optional[bool] = None
    observer_mode_enabled: Optional[bool] = None
    observer_no_signal_rounds: Optional[int] = Field(default=None, ge=1, le=50)
    auto_sell_enabled: Optional[bool] = None
    sell_full_position: Optional[bool] = None
    sell_order_quantity: Optional[int] = Field(default=None, ge=1)
    same_symbol_max_sells_per_day: Optional[int] = Field(default=None, ge=1, le=20)
    same_symbol_cooldown_minutes: Optional[int] = Field(default=None, ge=0, le=240)
    same_symbol_max_trades_per_day: Optional[int] = Field(default=None, ge=1, le=20)
    avoid_add_to_existing_position: Optional[bool] = None
    market: Optional[Literal["us", "hk", "cn"]] = None
    active_template: Optional[str] = None
    pair_mode: Optional[bool] = None
    interval_seconds: Optional[int] = Field(default=None, ge=30, le=3600)
    top_n: Optional[int] = Field(default=None, ge=1, le=30)
    kline: Optional[BacktestKline] = None
    backtest_days: Optional[int] = Field(default=None, ge=30, le=365)
    signal_bars_days: Optional[int] = Field(default=None, ge=30, le=365)
    order_quantity: Optional[int] = Field(default=None, ge=1)
    entry_rule: Optional[Literal["strategy_cross", "breakout", "mean_reversion"]] = None
    breakout_lookback_bars: Optional[int] = Field(default=None, ge=5, le=240)
    breakout_volume_ratio: Optional[float] = Field(default=None, ge=0, le=10)
    mean_reversion_rsi_threshold: Optional[float] = Field(default=None, ge=1, le=80)
    mean_reversion_deviation_pct: Optional[float] = Field(default=None, ge=0, le=30)
    exit_rules: Optional[list[str]] = None
    rule_priority: Optional[list[str]] = None
    hard_stop_pct: Optional[float] = Field(default=None, ge=0.1, le=50)
    take_profit_pct: Optional[float] = Field(default=None, ge=0.1, le=200)
    time_stop_hours: Optional[int] = Field(default=None, ge=1, le=720)
    sizer: Optional[SizerBody] = None
    cost_model: Optional[CostModelBody] = None
    max_daily_trades: Optional[int] = Field(default=None, ge=1, le=100)
    daily_loss_circuit_enabled: Optional[bool] = None
    daily_loss_limit_pct: Optional[float] = Field(default=None, ge=0, le=1)
    consecutive_loss_stop_enabled: Optional[bool] = None
    consecutive_loss_stop_count: Optional[int] = Field(default=None, ge=1, le=20)
    max_position_value: Optional[float] = Field(default=None, gt=0)
    max_total_exposure: Optional[float] = Field(default=None, ge=0, le=1)
    min_cash_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    same_direction_max_new_orders_per_scan: Optional[int] = Field(default=None, ge=1, le=20)
    max_concurrent_long_positions: Optional[int] = Field(default=None, ge=1, le=200)
    ml_filter_enabled: Optional[bool] = None
    ml_model_type: Optional[Literal["logreg", "random_forest", "gbdt"]] = None
    ml_threshold: Optional[float] = Field(default=None, ge=0.5, le=0.95)
    ml_horizon_days: Optional[int] = Field(default=None, ge=1, le=30)
    ml_train_ratio: Optional[float] = Field(default=None, ge=0.5, le=0.9)
    ml_walk_forward_windows: Optional[int] = Field(default=None, ge=1, le=12)
    ml_filter_cache_minutes: Optional[int] = Field(default=None, ge=0, le=1440)
    research_allocation_enabled: Optional[bool] = None
    research_allocation_max_age_minutes: Optional[int] = Field(default=None, ge=0, le=10080)
    research_allocation_snapshot_id: Optional[str] = None
    research_allocation_notional_scale: Optional[float] = Field(default=None, ge=0.01, le=3.0)
    merge_strategy_matrix_top3: Optional[bool] = None
    merge_strategy_matrix_top3_snapshot_id: Optional[str] = None
    strategies: Optional[list[str]] = None
    strategy_params_map: Optional[dict[str, dict[str, Any]]] = None
    universe: Optional[dict[str, list[str]]] = None
    pair_pool: Optional[dict[str, dict[str, str]]] = None

