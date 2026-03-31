from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body

from api import runtime_bridge as rt
from api.schemas_backtest import BacktestKline

router = APIRouter(tags=["auto-trader"])

@router.get("/auto-trader/status")
def auto_trader_status() -> dict[str, Any]:
    return rt.auto_trader_status()


@router.post("/auto-trader/config")
def auto_trader_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.auto_trader_config(body)


@router.get("/auto-trader/templates")
def auto_trader_templates() -> dict[str, Any]:
    return rt.auto_trader_templates()


@router.get("/auto-trader/config/policy")
def auto_trader_config_policy() -> dict[str, Any]:
    return rt.auto_trader_config_policy()


@router.post("/auto-trader/config/agent")
def auto_trader_config_agent_update(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.auto_trader_config_agent_update(body)


@router.post("/auto-trader/template/apply")
def auto_trader_template_apply(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.auto_trader_template_apply(body)


@router.get("/auto-trader/template/preview")
def auto_trader_template_preview(name: Literal["trend", "mean_reversion", "defensive"]) -> dict[str, Any]:
    return rt.auto_trader_template_preview(name=name)


@router.get("/auto-trader/config/export")
def auto_trader_export_config() -> dict[str, Any]:
    return rt.auto_trader_export_config()


@router.post("/auto-trader/config/import")
def auto_trader_import_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.auto_trader_import_config(body)


@router.get("/auto-trader/config/backups")
def auto_trader_config_backups() -> dict[str, Any]:
    return rt.auto_trader_config_backups()


@router.post("/auto-trader/config/rollback")
def auto_trader_config_rollback(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.auto_trader_config_rollback(body)


@router.get("/auto-trader/config/rollback/preview")
def auto_trader_config_rollback_preview(backup_id: str) -> dict[str, Any]:
    return rt.auto_trader_config_rollback_preview(backup_id=backup_id)


@router.get("/auto-trader/strong-stocks")
def auto_trader_strong_stocks(
    market: Literal["us", "hk", "cn"] = "us",
    limit: int = 8,
    kline: BacktestKline = "1d",
) -> dict[str, Any]:
    return rt.auto_trader_strong_stocks(market=market, limit=limit, kline=kline)


@router.get("/auto-trader/strategy-score")
def auto_trader_strategy_score(
    symbol: str,
    days: int = 120,
    kline: BacktestKline = "1d",
) -> dict[str, Any]:
    return rt.auto_trader_strategy_score(symbol=symbol, days=days, kline=kline)


@router.get("/auto-trader/strategies")
def auto_trader_strategies() -> dict[str, Any]:
    return rt.auto_trader_strategies()


@router.get("/auto-trader/pair-backtest")
def auto_trader_pair_backtest(
    market: Literal["us", "hk", "cn"] = "us",
    days: int = 180,
    kline: BacktestKline = "1d",
    initial_capital: float = 100000.0,
) -> dict[str, Any]:
    return rt.auto_trader_pair_backtest(market=market, days=days, kline=kline, initial_capital=initial_capital)


@router.post("/auto-trader/scan/run")
def auto_trader_scan_run() -> dict[str, Any]:
    return rt.auto_trader_scan_run()


@router.get("/auto-trader/signals")
def auto_trader_signals(status: str = "all") -> dict[str, Any]:
    return rt.auto_trader_signals(status=status)


@router.post("/auto-trader/signals/{signal_id}/confirm")
def auto_trader_confirm(signal_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.auto_trader_confirm(signal_id=signal_id, body=body)


@router.get("/auto-trader/metrics/recent")
def auto_trader_metrics_recent(limit: int = 200, event: str | None = None) -> dict[str, Any]:
    return rt.auto_trader_metrics_recent(limit=limit, event=event)


@router.get("/auto-trader/metrics/sla")
def auto_trader_metrics_sla(window_minutes: int = 5, limit: int = 2000) -> dict[str, Any]:
    return rt.auto_trader_metrics_sla(window_minutes=window_minutes, limit=limit)


@router.get("/auto-trader/research/status")
def auto_trader_research_status() -> dict[str, Any]:
    return rt.auto_trader_research_status()


@router.get("/auto-trader/research/snapshot")
def auto_trader_research_snapshot() -> dict[str, Any]:
    return rt.auto_trader_research_snapshot()


@router.get("/auto-trader/research/snapshots")
def auto_trader_research_snapshot_history_list(
    type: str,
    market: Literal["us", "hk", "cn"] = "us",
) -> dict[str, Any]:
    return rt.auto_trader_research_snapshot_history_list(history_type=type, market=market)


@router.get("/auto-trader/research/snapshots/{history_type}/{snapshot_id}")
def auto_trader_research_snapshot_history_get(
    history_type: str,
    snapshot_id: str,
    market: Literal["us", "hk", "cn"] = "us",
) -> dict[str, Any]:
    return rt.auto_trader_research_snapshot_history_get(history_type=history_type, snapshot_id=snapshot_id, market=market)


@router.post("/auto-trader/research/run")
def auto_trader_research_run(body: dict[str, Any] | None = Body(None)) -> dict[str, Any]:
    return rt.auto_trader_research_run(body=body)


@router.get("/auto-trader/research/tasks/{task_id}")
def auto_trader_research_task_status(task_id: str) -> dict[str, Any]:
    return rt.auto_trader_research_task_status(task_id=task_id)


@router.post("/auto-trader/research/tasks/{task_id}/cancel")
def auto_trader_research_task_cancel(task_id: str) -> dict[str, Any]:
    return rt.auto_trader_research_task_cancel(task_id=task_id)


@router.get("/auto-trader/research/model-compare")
def auto_trader_research_model_compare(top: int = 10) -> dict[str, Any]:
    return rt.auto_trader_research_model_compare(top=top)


@router.post("/auto-trader/research/strategy-matrix/run")
def auto_trader_research_strategy_matrix_run(body: dict[str, Any] | None = Body(None)) -> dict[str, Any]:
    return rt.auto_trader_research_strategy_matrix_run(body=body)


@router.get("/auto-trader/research/strategy-matrix/result")
def auto_trader_research_strategy_matrix_result(market: str | None = None) -> dict[str, Any]:
    return rt.auto_trader_research_strategy_matrix_result(market=market)


@router.post("/auto-trader/research/ml-matrix/run")
def auto_trader_research_ml_matrix_run(body: dict[str, Any] | None = Body(None)) -> dict[str, Any]:
    return rt.auto_trader_research_ml_matrix_run(body=body)


@router.get("/auto-trader/research/ml-matrix/result")
def auto_trader_research_ml_matrix_result(market: str | None = None) -> dict[str, Any]:
    return rt.auto_trader_research_ml_matrix_result(market=market)


@router.post("/auto-trader/research/ml-matrix/apply-to-config")
def auto_trader_research_ml_matrix_apply_to_config(body: dict[str, Any] | None = Body(None)) -> dict[str, Any]:
    return rt.auto_trader_research_ml_matrix_apply_to_config(body=body)


@router.get("/auto-trader/research/ab-report")
def auto_trader_research_ab_report() -> dict[str, Any]:
    return rt.auto_trader_research_ab_report()


@router.get("/auto-trader/research/ab-report/markdown")
def auto_trader_research_ab_report_markdown() -> dict[str, Any]:
    return rt.auto_trader_research_ab_report_markdown()

