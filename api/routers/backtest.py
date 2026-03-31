from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body

from api import runtime_bridge as rt
from api.schemas_backtest import BacktestKline

router = APIRouter(tags=["backtest"])


@router.get("/backtest/strategies")
def backtest_strategies_catalog() -> dict[str, Any]:
    return rt.backtest_strategies_catalog()


@router.get("/backtest/compare")
def backtest_compare(
    symbol: str = "RXRX.US",
    days: int = 180,
    periods: int = 0,
    kline: BacktestKline = "1d",
    initial_capital: float = 100000.0,
    execution_mode: Literal["next_open", "bar_close"] = "next_open",
    slippage_bps: float = 3.0,
    commission_bps: float | None = None,
    stamp_duty_bps: float | None = None,
    walk_forward_windows: int = 1,
    ml_filter_enabled: bool = False,
    ml_model_type: Literal["logreg", "random_forest", "gbdt"] = "logreg",
    ml_threshold: float = 0.55,
    ml_horizon_days: int = 5,
    ml_train_ratio: float = 0.7,
    include_trades: bool = False,
    trade_limit: int = 50,
    trade_offset: int = 0,
    strategy_key: str | None = None,
    include_best_kline: bool = False,
    use_server_kline_cache: bool = False,
) -> dict[str, Any]:
    return rt.backtest_compare(
        symbol=symbol,
        days=days,
        periods=periods,
        kline=kline,
        initial_capital=initial_capital,
        execution_mode=execution_mode,
        slippage_bps=slippage_bps,
        commission_bps=commission_bps,
        stamp_duty_bps=stamp_duty_bps,
        walk_forward_windows=walk_forward_windows,
        ml_filter_enabled=ml_filter_enabled,
        ml_model_type=ml_model_type,
        ml_threshold=ml_threshold,
        ml_horizon_days=ml_horizon_days,
        ml_train_ratio=ml_train_ratio,
        include_trades=include_trades,
        trade_limit=trade_limit,
        trade_offset=trade_offset,
        strategy_key=strategy_key,
        include_best_kline=include_best_kline,
        use_server_kline_cache=use_server_kline_cache,
    )


@router.post("/backtest/compare")
def backtest_compare_post(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.backtest_compare_post(body)


@router.post("/backtest/kline-cache/fetch")
def backtest_kline_cache_fetch(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.backtest_kline_cache_fetch(body)


@router.get("/backtest/kline-cache/status")
def backtest_kline_cache_status(
    symbol: str,
    kline: BacktestKline = "1d",
    periods: int = 0,
    days: int = 180,
) -> dict[str, Any]:
    return rt.backtest_kline_cache_status(symbol=symbol, kline=kline, periods=periods, days=days)


@router.delete("/backtest/kline-cache")
def backtest_kline_cache_delete(
    symbol: str,
    kline: BacktestKline = "1d",
    periods: int = 0,
    days: int = 180,
) -> dict[str, Any]:
    return rt.backtest_kline_cache_delete(symbol=symbol, kline=kline, periods=periods, days=days)

