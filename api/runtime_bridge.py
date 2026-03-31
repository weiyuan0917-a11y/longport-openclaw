from __future__ import annotations

import os
from typing import Any, Literal

from config.live_settings import live_settings
from config.notification_settings import resolve_feishu_app_config
from mcp_server.risk_manager import load_config, save_config
from runtime_process_utils import is_pid_alive, read_pid_file

from api.auto_trader_research import get_research_status
from api.schemas_auto_trader import (
    AutoTraderConfirmBody,
    AutoTraderConfigBody,
    AutoTraderImportBody,
    AutoTraderImportConfigBody,
    AutoTraderMlMatrixApplyBody,
    AutoTraderMlMatrixRunBody,
    AutoTraderResearchRunBody,
    AutoTraderRollbackBody,
    AutoTraderStrategyMatrixRunBody,
    AutoTraderTemplateApplyBody,
)
from api.schemas_backtest import BacktestCompareBody, BacktestKline, BacktestKlineCacheFetchBody
from api.schemas_fees_risk import FeeScheduleBody
from api.schemas_options_trade import OptionBacktestBody, OptionOrderBody, SubmitOrderBody
from api.schemas_setup import SetupConfigBody, SetupRiskConfigBody, SetupStartBody, SetupStopAllBody, SetupStopBody
from api.services import (
    apply_agent_policy_update,
    apply_auto_trader_config_update,
    apply_template_with_sync,
    apply_setup_env_updates,
    build_fee_schedule_response,
    build_risk_config_response,
    build_auto_trader_config_policy,
    build_auto_trader_status_response,
    build_longport_diagnostics_response,
    build_setup_config_response,
    build_setup_services_status,
    collect_longport_context_snapshot,
    estimate_fees,
    import_config_with_rollback,
    build_option_legs_or_400,
    build_option_submit_response,
    preview_rollback_safe,
    preview_template_safe,
    rollback_config_with_sync,
    save_fee_schedule,
    start_services,
    stop_all_services,
    stop_services,
)


def _m():
    # 延迟导入，避免在模块加载阶段触发循环依赖。
    from api import main as m

    return m


def _normalize_quantity_by_lot_size(qctx: Any, symbol: str, quantity: int) -> tuple[int, int]:
    """
    将下单数量修正为最小交易单位（lot_size）的整数倍。
    返回 (normalized_quantity, lot_size)。
    """
    qty = max(1, int(quantity))
    sym = str(symbol).strip().upper()
    # 用户要求：美股不做自动手数修正，保持原始数量。
    if sym.endswith(".US"):
        return qty, 1
    lot_size = 1
    try:
        st = qctx.static_info([sym])
        if st:
            lot_size = max(1, int(getattr(st[0], "lot_size", 1) or 1))
    except Exception:
        lot_size = 1
    if lot_size <= 1:
        return qty, 1
    if qty % lot_size == 0:
        return qty, lot_size
    # 向上取整，避免因不足一手导致持续下单失败（例如港股 100 -> 200）。
    normalized = ((qty + lot_size - 1) // lot_size) * lot_size
    return max(lot_size, normalized), lot_size


def setup_config() -> dict[str, Any]:
    m = _m()
    env_data = m._load_env_file(m.ENV_FILE)
    feishu_cfg = resolve_feishu_app_config(os.path.join(m.MCP_DIR, "notification_config.json"))
    return build_setup_config_response(env_data=env_data, feishu_cfg=feishu_cfg, mask_secret=m._mask_secret)


def setup_save_config(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = SetupConfigBody.model_validate(body if isinstance(body, dict) else {})
    payload = parsed.model_dump()
    env_data = m._load_env_file(m.ENV_FILE)
    changed = apply_setup_env_updates(payload=payload, env_data=env_data, env_var_map=m.ENV_VAR_MAP)
    m._save_env_file(m.ENV_FILE, env_data)
    live_settings.__init__()
    m.reset_contexts()
    return {"ok": True, "changed": changed, "restart_recommended": bool(changed)}


def setup_risk_config(body: dict[str, Any]) -> dict[str, Any]:
    parsed = SetupRiskConfigBody.model_validate(body if isinstance(body, dict) else {})
    cfg = load_config()
    updates = parsed.model_dump(exclude_none=True)
    for k, v in updates.items():
        setattr(cfg, k, v)
    save_config(cfg)
    return {"ok": True, "risk_config": cfg.to_dict()}


def setup_services_status() -> dict[str, Any]:
    m = _m()
    return build_setup_services_status(
        managed_processes=m._managed_processes,
        feishu_pid_file=m.FEISHU_PID_FILE,
        auto_trader_supervisor_pid_file=m.AUTO_TRADER_SUPERVISOR_PID_FILE,
        auto_runtime=m._auto_trader_runtime_status(),
    )


def setup_longport_diagnostics(probe: bool = False) -> dict[str, Any]:
    m = _m()
    probe_ok = None
    probe_error = None
    if probe:
        try:
            m.ensure_contexts()
            probe_ok = True
        except Exception as e:
            probe_ok = False
            probe_error = str(e)
    runtime_ctx = m._collect_longport_runtime_state()
    with m._ctx_lock:
        ctx_snapshot = collect_longport_context_snapshot(
            quote_ready=bool(runtime_ctx.get("quote_ready")),
            trade_ready=bool(runtime_ctx.get("trade_ready")),
            connection_limit=m.LONGPORT_CONNECTION_LIMIT,
            last_error=runtime_ctx.get("last_error"),
            last_init_at=runtime_ctx.get("last_init_at"),
        )
        active_connections = int(ctx_snapshot.get("active_connections", 0))
        usage_pct = float(ctx_snapshot.get("usage_pct", 0.0))
        quote_ready = bool(ctx_snapshot.get("quote_ready"))
        trade_ready = bool(ctx_snapshot.get("trade_ready"))
        last_error = ctx_snapshot.get("last_error")
        last_init_at = ctx_snapshot.get("last_init_at")
    mcp_pid = read_pid_file(m.MCP_PID_FILE)
    feishu_pid = read_pid_file(m.FEISHU_PID_FILE)
    auto_trader_pid = read_pid_file(m.AUTO_TRADER_PID_FILE)
    auto_trader_supervisor_pid = read_pid_file(m.AUTO_TRADER_SUPERVISOR_PID_FILE)
    return build_longport_diagnostics_response(
        connection_limit=m.LONGPORT_CONNECTION_LIMIT,
        active_connections=active_connections,
        usage_pct=usage_pct,
        quote_ready=quote_ready,
        trade_ready=trade_ready,
        last_error=last_error,
        last_init_at=last_init_at,
        probe_requested=probe,
        probe_ok=probe_ok,
        probe_error=probe_error,
        mcp_pid=mcp_pid,
        feishu_pid=feishu_pid,
        auto_trader_pid=auto_trader_pid,
        auto_trader_supervisor_pid=auto_trader_supervisor_pid,
        mcp_running=is_pid_alive(mcp_pid),
        feishu_running=is_pid_alive(feishu_pid),
        auto_trader_running=is_pid_alive(auto_trader_pid),
        auto_trader_supervisor_running=is_pid_alive(auto_trader_supervisor_pid),
        mcp_pid_file=m.MCP_PID_FILE,
        feishu_pid_file=m.FEISHU_PID_FILE,
        auto_trader_pid_file=m.AUTO_TRADER_PID_FILE,
        auto_trader_supervisor_pid_file=m.AUTO_TRADER_SUPERVISOR_PID_FILE,
        auto_trader_supervisor_status_file=m.AUTO_TRADER_SUPERVISOR_STATUS_FILE,
        auto_trader_worker_runtime_file=m.AUTO_TRADER_WORKER_RUNTIME_FILE,
        gateway_enabled=m._gateway_enabled(),
    )


def setup_start_services(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = SetupStartBody.model_validate(body if isinstance(body, dict) else {})
    return start_services(
        start_feishu_bot=bool(parsed.start_feishu_bot),
        enable_auto_trader=bool(parsed.enable_auto_trader),
        auto_trader=m.auto_trader,
        start_auto_trader_worker=m._start_auto_trader_worker,
        managed_processes=m._managed_processes,
        root=m.ROOT,
        mcp_dir=m.MCP_DIR,
        win_subprocess_silent_kwargs=m._win_subprocess_silent_kwargs,
    )


def setup_stop_services(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = SetupStopBody.model_validate(body if isinstance(body, dict) else {})
    return stop_services(
        stop_auto_trader=bool(parsed.stop_auto_trader),
        stop_feishu_bot=bool(parsed.stop_feishu_bot),
        auto_trader=m.auto_trader,
        stop_auto_trader_worker=m._stop_auto_trader_worker,
        stop_feishu_bot_managed_or_pidfile=m._stop_feishu_bot_managed_or_pidfile,
        wait_auto_trader_stopped=m._wait_auto_trader_processes_stopped,
        stop_confirm_timeout_seconds=8.0,
    )


def setup_stop_all_services(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = SetupStopAllBody.model_validate(body if isinstance(body, dict) else {})
    return stop_all_services(
        stop_backend=bool(parsed.stop_backend),
        stop_frontend=bool(parsed.stop_frontend),
        stop_feishu_bot=bool(parsed.stop_feishu_bot),
        stop_auto_trader=bool(parsed.stop_auto_trader),
        auto_trader=m.auto_trader,
        stop_auto_trader_worker=m._stop_auto_trader_worker,
        stop_feishu_bot_managed_or_pidfile=m._stop_feishu_bot_managed_or_pidfile,
        watchdog_pause_file=m.WATCHDOG_PAUSE_FILE,
        watchdog_pid_file=m.WATCHDOG_PID_FILE,
        read_pid_file=read_pid_file,
        is_pid_alive=is_pid_alive,
        win_subprocess_silent_kwargs=m._win_subprocess_silent_kwargs,
    )


def fees_schedule() -> dict[str, Any]:
    m = _m()
    return build_fee_schedule_response(m.get_fee_schedule())


def fees_schedule_default() -> dict[str, Any]:
    m = _m()
    return build_fee_schedule_response(m.get_default_fee_schedule())


def fees_schedule_save(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = FeeScheduleBody.model_validate(body if isinstance(body, dict) else {})
    return save_fee_schedule(
        schedule_payload=parsed.schedule,
        set_fee_schedule=m.set_fee_schedule,
        save_fee_schedule_file=m._save_fee_schedule_file,
        fee_schedule_file=m.FEE_SCHEDULE_FILE,
    )


def fees_estimate(
    *,
    asset_class: Literal["stock", "us_option"] = "stock",
    market: Literal["HK", "US", "CN", "OTHER"] = "US",
    side: Literal["buy", "sell"] = "buy",
    quantity: int = 100,
    price: float = 1.0,
) -> dict[str, Any]:
    m = _m()
    return estimate_fees(
        asset_class=asset_class,
        market=market,
        side=side,
        quantity=quantity,
        price=price,
        estimate_stock_order_fee=m.estimate_stock_order_fee,
        estimate_us_option_order_fee=m.estimate_us_option_order_fee,
    )


def risk_config() -> dict[str, Any]:
    return build_risk_config_response(load_config=load_config)


def dashboard_summary() -> dict[str, Any]:
    m = _m()
    pool = m.ThreadPoolExecutor(max_workers=4)
    try:
        fut_analysis = pool.submit(m.get_comprehensive_analysis)
        fut_sectors = pool.submit(m.get_sector_rotation, 5)
        fut_cn_hk = pool.submit(
            m._market_snap,
            [
                ("000001.SH", "上证综指"),
                ("399001.SZ", "深证成指"),
                ("HSI.HK", "恒生指数"),
                ("HSTECH.HK", "恒生科技"),
            ],
        )
        fut_us = pool.submit(
            m._market_snap,
            [
                ("SPY.US", "标普500"),
                ("QQQ.US", "纳指100"),
                ("DIA.US", "道指"),
            ],
        )
        try:
            analysis = fut_analysis.result(timeout=3.0)
        except Exception:
            analysis = {
                "market_environment": "数据刷新中",
                "strategy_recommendation": "建议稍后重试",
                "score": 0,
                "indicators": {},
                "analysis_time": m.datetime.now().isoformat(),
                "data_source": "fallback",
            }
        try:
            sectors = fut_sectors.result(timeout=3.0)
        except Exception:
            sectors = {
                "data_source": "fallback",
                "data_source_label": "兜底",
                "top_performers": [],
                "bottom_performers": [],
            }
        try:
            cn_hk = fut_cn_hk.result(timeout=2.5)
        except Exception:
            cn_hk = []
        try:
            us = fut_us.result(timeout=2.5)
        except Exception:
            us = []
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {
        "markets": {"cn_hk": cn_hk, "us": us},
        "analysis": analysis,
        "sector_data_source": sectors.get("data_source", "unknown"),
        "sector_data_source_label": sectors.get("data_source_label", "未知"),
        "sector_age_seconds": sectors.get("age_seconds"),
        "sector_last_refresh_ts": sectors.get("last_refresh_ts"),
        "sector_top3": sectors.get("top_performers", [])[:3],
        "sector_bottom3": sectors.get("bottom_performers", [])[:3],
    }


def market_analysis() -> dict[str, Any]:
    m = _m()
    return m.get_comprehensive_analysis()


def market_sectors(days: int = 5) -> dict[str, Any]:
    m = _m()
    return m.get_sector_rotation(days=days)


def signals(symbol: str = "RXRX.US") -> dict[str, Any]:
    m = _m()
    from api.signal_center_signals import analyze_signal_center_from_closes

    bars = m._fetch_bars_calendar_days(symbol, 90)
    if len(bars) < 25:
        raise m.HTTPException(status_code=400, detail="历史数据不足")
    closes = [float(b.close) for b in bars]
    snap = analyze_signal_center_from_closes(closes)
    if not snap:
        raise m.HTTPException(status_code=400, detail="历史数据不足")
    signal_flags = snap["signals"]
    rt = m._quote_last(symbol)
    latest_price = closes[-1]
    latest_price_type = "K线收盘"
    latest_price_source = "kline_close"
    if rt and float(rt.get("last", 0) or 0) > 0:
        latest_price = float(rt["last"])
        latest_price_type = str(rt.get("price_type", "盘中"))
        latest_price_source = "realtime_quote"
    return {
        "symbol": symbol,
        "latest_close": closes[-1],
        "latest_price": round(float(latest_price), 4),
        "latest_price_type": latest_price_type,
        "latest_price_source": latest_price_source,
        "rsi14": snap["rsi14"],
        "ma5": snap["ma5"],
        "ma20": snap["ma20"],
        "signals": signal_flags,
    }


def backtest_strategies_catalog() -> dict[str, Any]:
    m = _m()
    return {"items": m.list_strategy_metadata()}


def backtest_compare(
    *,
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
    m = _m()
    sym = str(symbol or "").strip().upper()
    bars = m._resolve_bars_for_backtest_compare(sym, periods, days, kline, None, use_server_kline_cache=bool(use_server_kline_cache))
    return m._backtest_compare_core(
        sym,
        bars,
        periods=periods,
        days=days,
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
        strategy_params_map=None,
        include_bars_in_response=False,
    )


def backtest_compare_post(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = BacktestCompareBody.model_validate(body if isinstance(body, dict) else {})
    sym = str(parsed.symbol or "").strip().upper()
    client_list = m._parse_client_bars_for_backtest(parsed.bars) if parsed.bars else []
    client_bars = client_list if client_list else None
    bars = m._resolve_bars_for_backtest_compare(
        sym,
        parsed.periods,
        parsed.days,
        parsed.kline,
        client_bars,
        use_server_kline_cache=bool(parsed.use_server_kline_cache),
    )
    sp_map = parsed.strategy_params if isinstance(parsed.strategy_params, dict) else None
    return m._backtest_compare_core(
        sym,
        bars,
        periods=parsed.periods,
        days=parsed.days,
        kline=parsed.kline,
        initial_capital=parsed.initial_capital,
        execution_mode=parsed.execution_mode,
        slippage_bps=parsed.slippage_bps,
        commission_bps=parsed.commission_bps,
        stamp_duty_bps=parsed.stamp_duty_bps,
        walk_forward_windows=parsed.walk_forward_windows,
        ml_filter_enabled=parsed.ml_filter_enabled,
        ml_model_type=parsed.ml_model_type,
        ml_threshold=parsed.ml_threshold,
        ml_horizon_days=parsed.ml_horizon_days,
        ml_train_ratio=parsed.ml_train_ratio,
        include_trades=parsed.include_trades,
        trade_limit=parsed.trade_limit,
        trade_offset=parsed.trade_offset,
        strategy_key=parsed.strategy_key,
        include_best_kline=parsed.include_best_kline,
        strategy_params_map=sp_map,
        include_bars_in_response=bool(parsed.include_bars_in_response),
    )


def backtest_kline_cache_fetch(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = BacktestKlineCacheFetchBody.model_validate(body if isinstance(body, dict) else {})
    sym = str(parsed.symbol or "").strip().upper()
    if not sym:
        raise m.HTTPException(status_code=400, detail="symbol_required")
    periods = max(0, int(parsed.periods or 0))
    days = max(1, min(3650, int(parsed.days or 180)))
    kline = parsed.kline
    path = m._kline_server_cache_path(sym, kline, periods, days)
    if not parsed.force_refresh and m.os.path.isfile(path):
        bars0, meta0 = m._read_server_kline_cache_file(path)
        if bars0 and (periods <= 0 or len(bars0) >= periods):
            use = bars0[-periods:] if periods > 0 and len(bars0) > periods else bars0
            return {
                "ok": True,
                "cached": True,
                "symbol": sym,
                "kline": str(kline),
                "periods": periods,
                "days": days,
                "bar_count": len(use),
                "cache_path": path,
                "meta": meta0,
            }
    if periods > 0:
        bars = m._fetch_bars_by_periods(sym, periods, kline)
        bars = bars[-periods:] if len(bars) > periods else bars
    else:
        bars = m._fetch_bars_calendar_days(sym, days, kline)
    if not bars:
        raise m.HTTPException(status_code=400, detail="无法获取历史数据")
    m._write_server_kline_cache_file(path, symbol=sym, kline=str(kline), periods=periods, days=days, bars=bars)
    return {
        "ok": True,
        "cached": False,
        "symbol": sym,
        "kline": str(kline),
        "periods": periods,
        "days": days,
        "bar_count": len(bars),
        "cache_path": path,
        "meta": {"saved_at": m.datetime.now(m.timezone.utc).isoformat(), "bar_count": len(bars)},
    }


def backtest_kline_cache_status(
    *,
    symbol: str,
    kline: BacktestKline = "1d",
    periods: int = 0,
    days: int = 180,
) -> dict[str, Any]:
    m = _m()
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise m.HTTPException(status_code=400, detail="symbol_required")
    periods = max(0, int(periods))
    days = max(1, min(3650, int(days)))
    path = m._kline_server_cache_path(sym, kline, periods, days)
    bars, meta = m._read_server_kline_cache_file(path)
    ok = bool(bars) and (periods <= 0 or len(bars) >= periods)
    return {
        "exists": bool(bars),
        "ready": ok,
        "symbol": sym,
        "kline": str(kline),
        "periods": periods,
        "days": days,
        "bar_count": len(bars) if bars else 0,
        "cache_path": path,
        "meta": meta,
    }


def backtest_kline_cache_delete(
    *,
    symbol: str,
    kline: BacktestKline = "1d",
    periods: int = 0,
    days: int = 180,
) -> dict[str, Any]:
    m = _m()
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise m.HTTPException(status_code=400, detail="symbol_required")
    periods = max(0, int(periods))
    days = max(1, min(3650, int(days)))
    path = m._kline_server_cache_path(sym, kline, periods, days)
    base = m.os.path.abspath(m.KLINE_SERVER_CACHE_DIR)
    abspath = m.os.path.abspath(path)
    if not abspath.startswith(base + m.os.sep) and abspath != base:
        raise m.HTTPException(status_code=400, detail="invalid_cache_path")
    removed = False
    try:
        if m.os.path.isfile(path):
            m.os.remove(path)
            removed = True
    except OSError:
        pass
    return {"ok": True, "removed": removed, "cache_path": path}


def trade_account() -> dict[str, Any]:
    m = _m()
    gw = m._gateway_get_json("/trade/account")
    if isinstance(gw, dict) and all(k in gw for k in ("net_assets", "buy_power", "currency")):
        return gw
    _, tctx = m.ensure_contexts()
    bl = tctx.account_balance()
    if not bl:
        raise m.HTTPException(status_code=400, detail="账户信息为空")
    b = bl[0]
    return {"net_assets": float(b.net_assets), "buy_power": float(b.buy_power), "currency": str(b.currency)}


def options_expiries(symbol: str) -> dict[str, Any]:
    m = _m()
    qctx, _ = m.ensure_contexts()
    return m.fetch_option_expiries(qctx, symbol)


def options_chain(
    *,
    symbol: str,
    expiry_date: str | None = None,
    min_strike: float | None = None,
    max_strike: float | None = None,
    standard_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    m = _m()
    qctx, _ = m.ensure_contexts()
    return m.fetch_option_chain(
        quote_ctx=qctx,
        symbol=symbol,
        expiry_date=expiry_date,
        min_strike=min_strike,
        max_strike=max_strike,
        standard_only=standard_only,
        limit=limit,
        offset=offset,
    )


def options_fee_estimate(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = OptionOrderBody.model_validate(body if isinstance(body, dict) else {})
    legs = build_option_legs_or_400(body=parsed, build_order_legs=m.build_order_legs)
    estimate = m.estimate_option_fee_for_legs(legs)
    return {"estimate": estimate}


def options_order(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = OptionOrderBody.model_validate(body if isinstance(body, dict) else {})
    m._ensure_l3_confirmation(parsed.confirmation_token)
    _, tctx = m.ensure_contexts()
    bl = tctx.account_balance()
    b = bl[0] if bl else None
    available_cash = float(b.buy_power) if b else 0.0
    legs = build_option_legs_or_400(body=parsed, build_order_legs=m.build_order_legs)
    submit_result = m.submit_option_order_with_risk(
        trade_ctx=tctx,
        legs=legs,
        available_cash=available_cash,
        max_loss_threshold=parsed.max_loss_threshold,
        max_capital_usage=parsed.max_capital_usage,
    )
    return build_option_submit_response(submit_result)


def options_orders(status: str = "all") -> dict[str, Any]:
    m = _m()
    _, tctx = m.ensure_contexts()
    return m.svc_get_option_orders(tctx, status=status)


def options_positions() -> dict[str, Any]:
    m = _m()
    qctx, tctx = m.ensure_contexts()
    return m.svc_get_option_positions(tctx, qctx)


def options_backtest(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = OptionBacktestBody.model_validate(body if isinstance(body, dict) else {})
    return m.svc_run_option_backtest(
        fetch_bars_fn=lambda symbol, days: m._fetch_bars_calendar_days(symbol, days, "1d"),
        symbol=parsed.symbol,
        template=parsed.template,
        days=parsed.days,
        holding_days=parsed.holding_days,
        contracts=parsed.contracts,
        width_pct=parsed.width_pct,
    )


def trade_positions() -> dict[str, Any]:
    m = _m()
    gw = m._gateway_get_json("/trade/positions")
    if isinstance(gw, dict) and isinstance(gw.get("positions"), list):
        return gw
    qctx, tctx = m.ensure_contexts()
    pos = tctx.stock_positions()
    rows: list[dict[str, Any]] = []
    for ch in pos.channels:
        for p in ch.positions:
            cur = 0.0
            price_type = "-"
            try:
                q = qctx.quote([p.symbol])
                if q:
                    cur, price_type = m._get_realtime_price(q[0])
            except Exception:
                pass
            qty = float(p.quantity)
            cost = float(p.cost_price)
            value = qty * cur
            pnl = value - qty * cost
            rows.append(
                {
                    "symbol": p.symbol,
                    "quantity": qty,
                    "cost_price": cost,
                    "current_price": cur,
                    "pnl": round(pnl, 2),
                    "price_type": price_type,
                }
            )
    return {"positions": rows}


def trade_orders(status: str = "all") -> dict[str, Any]:
    m = _m()
    gw = m._gateway_get_json("/trade/orders", {"status": status})
    if isinstance(gw, dict) and isinstance(gw.get("orders"), list):
        return gw
    _, tctx = m.ensure_contexts()
    allowed = {"active": {"New", "PartialFilled"}, "filled": {"Filled"}, "cancelled": {"Canceled"}}.get(status)
    orders = []
    for o in tctx.today_orders():
        s = str(o.status)
        if allowed and s not in allowed:
            continue
        orders.append(
            {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": str(o.side),
                "quantity": float(o.quantity),
                "price": float(o.price) if o.price else None,
                "status": s,
            }
        )
    return {"orders": orders}


def trade_submit_order(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = SubmitOrderBody.model_validate(body if isinstance(body, dict) else {})
    qctx, tctx = m.ensure_contexts()
    normalized_qty, lot_size = _normalize_quantity_by_lot_size(qctx, parsed.symbol, parsed.quantity)
    qty_adjusted = int(normalized_qty) != int(parsed.quantity)
    payload = parsed.model_dump(exclude_none=True)
    payload["quantity"] = int(normalized_qty)
    ok, gw = m._gateway_post_json(
        "/trade/order",
        payload,
        timeout=max(m.LONGPORT_GATEWAY_TIMEOUT_SECONDS, 12.0),
    )
    if ok and isinstance(gw, dict) and gw.get("order_id"):
        if qty_adjusted:
            gw["requested_quantity"] = int(parsed.quantity)
            gw["submitted_quantity"] = int(normalized_qty)
            gw["lot_size"] = int(lot_size)
            gw["quantity_adjusted"] = True
        return gw
    m._assert_us_order_session_allowed(parsed.symbol)
    cp = parsed.price or 0.0
    if not cp and parsed.action == "buy":
        qs = qctx.quote([parsed.symbol])
        cp = m._get_realtime_price(qs[0])[0] if qs else 0.0
    if parsed.action == "buy" and cp > 0:
        bl = tctx.account_balance()
        b = bl[0] if bl else None
        ta = float(b.net_assets) if b else 0.0
        ac = float(b.buy_power) if b else 0.0
        ev = 0.0
        for ch in tctx.stock_positions().channels:
            for p in ch.positions:
                if p.symbol == parsed.symbol:
                    ev = m.trade_value(parsed.symbol, float(p.quantity), float(p.cost_price))
        rr = m.get_manager().full_check_before_order(
            symbol=parsed.symbol,
            action=parsed.action,
            quantity=int(normalized_qty),
            price=cp,
            total_assets=ta,
            available_cash=ac,
            existing_position_value=ev,
        )
        if not rr["passed"]:
            raise m.HTTPException(status_code=400, detail={"risk_blocks": rr["blocks"]})
    side = m.OrderSide.Buy if parsed.action == "buy" else m.OrderSide.Sell
    resp = tctx.submit_order(
        symbol=parsed.symbol,
        order_type=m.OrderType.LO if parsed.price else m.OrderType.MO,
        side=side,
        submitted_quantity=int(normalized_qty),
        time_in_force=m.TimeInForceType.Day,
        **({} if not parsed.price else {"submitted_price": m.Decimal(str(parsed.price))}),
    )
    out: dict[str, Any] = {"order_id": resp.order_id}
    if qty_adjusted:
        out.update(
            {
                "requested_quantity": int(parsed.quantity),
                "submitted_quantity": int(normalized_qty),
                "lot_size": int(lot_size),
                "quantity_adjusted": True,
            }
        )
    return out


def trade_cancel_order(order_id: str) -> dict[str, Any]:
    m = _m()
    ok, gw = m._gateway_post_json(f"/trade/order/{order_id}/cancel", {}, timeout=max(m.LONGPORT_GATEWAY_TIMEOUT_SECONDS, 10.0))
    if ok and isinstance(gw, dict) and bool(gw.get("ok")):
        return gw
    _, tctx = m.ensure_contexts()
    tctx.cancel_order(order_id)
    return {"ok": True, "order_id": order_id}


def auto_trader_status() -> dict[str, Any]:
    m = _m()
    return build_auto_trader_status_response(
        status=m.auto_trader.get_status(),
        runtime=m._auto_trader_runtime_status(),
        research=get_research_status(),
        config=m.auto_trader.get_config(),
    )


def auto_trader_config(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderConfigBody.model_validate(body if isinstance(body, dict) else {})
    payload = {k: v for k, v in parsed.model_dump().items() if v is not None}
    return apply_auto_trader_config_update(
        payload=payload,
        update_config=m.auto_trader.update_config,
        sync_worker=m._sync_auto_trader_worker_with_config,
    )


def auto_trader_templates() -> dict[str, Any]:
    m = _m()
    return {"items": m.auto_trader.list_templates()}


def auto_trader_config_policy() -> dict[str, Any]:
    m = _m()
    return build_auto_trader_config_policy(locked_fields=m.AGENT_POLICY_LOCKED_FIELDS, field_rules=m.AGENT_POLICY_FIELD_RULES)


def auto_trader_config_agent_update(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderConfigBody.model_validate(body if isinstance(body, dict) else {})
    raw_payload = {k: v for k, v in parsed.model_dump().items() if v is not None}
    return apply_agent_policy_update(
        raw_payload=raw_payload,
        current_config=m.auto_trader.get_config(),
        validate_update=m._validate_agent_policy_update,
        locked_fields=m.AGENT_POLICY_LOCKED_FIELDS,
        allowed_field_rules=m.AGENT_POLICY_FIELD_RULES,
        update_config=m.auto_trader.update_config,
        sync_worker=m._sync_auto_trader_worker_with_config,
    )


def auto_trader_template_apply(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderTemplateApplyBody.model_validate(body if isinstance(body, dict) else {})
    return apply_template_with_sync(
        template_name=parsed.name,
        apply_template=m.auto_trader.apply_template,
        sync_worker=m._sync_auto_trader_worker_with_config,
    )


def auto_trader_template_preview(name: Literal["trend", "mean_reversion", "defensive"]) -> dict[str, Any]:
    m = _m()
    return preview_template_safe(template_name=name, preview_template=m.auto_trader.preview_template)


def auto_trader_export_config() -> dict[str, Any]:
    m = _m()
    return {"config": m.auto_trader.get_config()}


def auto_trader_import_config(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderImportBody.model_validate(body if isinstance(body, dict) else {})
    return import_config_with_rollback(
        config_obj=dict(parsed.config or {}),
        current_config=m.auto_trader.get_config(),
        validate_import_config=lambda cfg: AutoTraderImportConfigBody.model_validate(cfg).model_dump(exclude_none=True),
        update_config=m.auto_trader.update_config,
        sync_worker=m._sync_auto_trader_worker_with_config,
    )


def auto_trader_config_backups() -> dict[str, Any]:
    m = _m()
    return {"items": m.auto_trader.list_config_backups()}


def auto_trader_config_rollback(body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderRollbackBody.model_validate(body if isinstance(body, dict) else {})
    return rollback_config_with_sync(
        backup_id=parsed.backup_id,
        rollback_config=m.auto_trader.rollback_config,
        sync_worker=m._sync_auto_trader_worker_with_config,
    )


def auto_trader_config_rollback_preview(backup_id: str) -> dict[str, Any]:
    m = _m()
    return preview_rollback_safe(backup_id=backup_id, preview_rollback=m.auto_trader.preview_rollback)


def auto_trader_strong_stocks(
    market: Literal["us", "hk", "cn"] = "us",
    limit: int = 8,
    kline: BacktestKline = "1d",
) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_strong_stocks(market=market, limit=limit, kline=kline)


def auto_trader_strategy_score(
    symbol: str,
    days: int = 120,
    kline: BacktestKline = "1d",
) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_strategy_score(symbol=symbol, days=days, kline=kline)


def auto_trader_strategies() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_strategies()


def auto_trader_pair_backtest(
    market: Literal["us", "hk", "cn"] = "us",
    days: int = 180,
    kline: BacktestKline = "1d",
    initial_capital: float = 100000.0,
) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_pair_backtest(market=market, days=days, kline=kline, initial_capital=initial_capital)


def auto_trader_scan_run() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_scan_run()


def auto_trader_signals(status: str = "all") -> dict[str, Any]:
    m = _m()
    return m.auto_trader_signals(status=status)


def auto_trader_confirm(signal_id: str, body: dict[str, Any]) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderConfirmBody.model_validate(body if isinstance(body, dict) else {})
    return m.auto_trader_confirm(signal_id=signal_id, body=parsed)


def auto_trader_metrics_recent(limit: int = 200, event: str | None = None) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_metrics_recent(limit=limit, event=event)


def auto_trader_metrics_sla(window_minutes: int = 5, limit: int = 2000) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_metrics_sla(window_minutes=window_minutes, limit=limit)


def auto_trader_research_status() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_status()


def auto_trader_research_snapshot() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_snapshot()


def auto_trader_research_snapshot_history_list(history_type: str, market: Literal["us", "hk", "cn"] = "us") -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_snapshot_history_list(history_type=history_type, market=market)


def auto_trader_research_snapshot_history_get(
    history_type: str,
    snapshot_id: str,
    market: Literal["us", "hk", "cn"] = "us",
) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_snapshot_history_get(
        history_type=history_type,
        snapshot_id=snapshot_id,
        market=market,
    )


def auto_trader_research_run(body: dict[str, Any] | None = None) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderResearchRunBody.model_validate(body if isinstance(body, dict) else {}) if body is not None else None
    return m.auto_trader_research_run(body=parsed)


def auto_trader_research_task_status(task_id: str) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_task_status(task_id=task_id)


def auto_trader_research_task_cancel(task_id: str) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_task_cancel(task_id=task_id)


def auto_trader_research_model_compare(top: int = 10) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_model_compare(top=top)


def auto_trader_research_strategy_matrix_run(body: dict[str, Any] | None = None) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderStrategyMatrixRunBody.model_validate(body if isinstance(body, dict) else {}) if body is not None else None
    return m.auto_trader_research_strategy_matrix_run(body=parsed)


def auto_trader_research_strategy_matrix_result(market: str | None = None) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_strategy_matrix_result(market=market)


def auto_trader_research_ml_matrix_run(body: dict[str, Any] | None = None) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderMlMatrixRunBody.model_validate(body if isinstance(body, dict) else {}) if body is not None else None
    return m.auto_trader_research_ml_matrix_run(body=parsed)


def auto_trader_research_ml_matrix_result(market: str | None = None) -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_ml_matrix_result(market=market)


def auto_trader_research_ml_matrix_apply_to_config(body: dict[str, Any] | None = None) -> dict[str, Any]:
    m = _m()
    parsed = AutoTraderMlMatrixApplyBody.model_validate(body if isinstance(body, dict) else {}) if body is not None else None
    return m.auto_trader_research_ml_matrix_apply_to_config(body=parsed)


def auto_trader_research_ab_report() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_ab_report()


def auto_trader_research_ab_report_markdown() -> dict[str, Any]:
    m = _m()
    return m.auto_trader_research_ab_report_markdown()

