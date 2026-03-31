from __future__ import annotations

from typing import Any, Callable

from mcp_server.backtest_engine import BacktestEngine
from mcp_server.strategies import get_strategy


DEFAULT_PAIR_POOL: dict[str, dict[str, str]] = {
    "us": {
        "SPY.US": "SH.US",
        "QQQ.US": "PSQ.US",
        "DIA.US": "DOG.US",
    },
    "hk": {},
    "cn": {},
}


def normalize_pair_pool(raw: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    out = {k: dict(v) for k, v in DEFAULT_PAIR_POOL.items()}
    if not isinstance(raw, dict):
        return out
    for market in ("us", "hk", "cn"):
        rows = raw.get(market)
        if not isinstance(rows, dict):
            continue
        cleaned: dict[str, str] = {}
        for long_sym, short_sym in rows.items():
            l = str(long_sym).strip().upper()
            s = str(short_sym).strip().upper()
            if l and s and l != s:
                cleaned[l] = s
        out[market] = cleaned
    return out


def flatten_pair_symbols(pair_pool: dict[str, dict[str, str]], market: str) -> list[str]:
    rows = pair_pool.get(market.lower(), {})
    symbols: list[str] = []
    seen: set[str] = set()
    for long_sym, short_sym in rows.items():
        for sym in (long_sym, short_sym):
            if sym not in seen:
                seen.add(sym)
                symbols.append(sym)
    return symbols


def run_pair_portfolio_backtest(
    fetch_bars: Callable[[str, int, str], list[Any]],
    pair_pool: dict[str, dict[str, str]],
    market: str,
    strategies: list[str],
    days: int = 180,
    kline: str = "1d",
    initial_capital: float = 100000.0,
    max_single_ratio: float = 0.2,
    max_total_ratio: float = 0.5,
) -> dict[str, Any]:
    """
    Minimal pair-portfolio backtest:
    - Per pair, evaluate long/short ETF independently
    - pick better side by composite score
    - aggregate selected sleeves with risk budget caps
    """
    pairs = pair_pool.get(market.lower(), {})
    if not pairs:
        return {
            "market": market,
            "days": days,
            "kline": kline,
            "initial_capital": initial_capital,
            "error": "pair pool is empty",
            "selected_pairs": [],
        }

    pair_rows: list[dict[str, Any]] = []
    invalid_symbols: list[dict[str, str]] = []

    def _serialize_trades(trades: list[Any], limit: int = 200) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in list(trades or [])[: max(0, int(limit))]:
            out.append(
                {
                    "entry_date": str(getattr(t, "entry_date", "")),
                    "exit_date": str(getattr(t, "exit_date", "")),
                    "entry_price": round(float(getattr(t, "entry_price", 0.0) or 0.0), 4),
                    "exit_price": round(float(getattr(t, "exit_price", 0.0) or 0.0), 4),
                    "quantity": int(getattr(t, "quantity", 0) or 0),
                    "pnl": round(float(getattr(t, "pnl", 0.0) or 0.0), 2),
                    "pnl_pct": round(float(getattr(t, "pnl_pct", 0.0) or 0.0), 2),
                    "hold_days": int(getattr(t, "hold_days", 0) or 0),
                }
            )
        return out

    for long_sym, short_sym in pairs.items():
        side_results: list[dict[str, Any]] = []
        for sym in (long_sym, short_sym):
            try:
                bars = fetch_bars(sym, days, kline)
            except Exception as e:
                err = str(e)
                side_results.append({"symbol": sym, "error": err, "composite_score": -9999.0})
                # LongPort invalid symbol should not crash whole portfolio backtest.
                if "invalid symbol" in err.lower():
                    invalid_symbols.append({"symbol": sym, "error": err})
                continue
            if not bars:
                side_results.append({"symbol": sym, "error": "no bars"})
                continue
            best: dict[str, Any] | None = None
            for sname in strategies:
                try:
                    sfn = get_strategy(sname, None)
                    engine = BacktestEngine(
                        bars=bars,
                        symbol=sym,
                        strategy_name=sfn.__name__,
                        strategy_fn=sfn,
                        initial_capital=100000.0,
                    )
                    r = engine.run()
                    composite = float(r.total_return_pct) - 0.5 * float(r.max_drawdown_pct) + 5.0 * float(r.sharpe_ratio)
                    row = {
                        "symbol": sym,
                        "strategy": sname,
                        "strategy_label": r.strategy_name,
                        "total_return_pct": round(float(r.total_return_pct), 2),
                        "max_drawdown_pct": round(float(r.max_drawdown_pct), 2),
                        "sharpe_ratio": round(float(r.sharpe_ratio), 2),
                        "trade_count": int(r.total_trades),
                        "trade_history": _serialize_trades(getattr(r, "trades", [])),
                        "composite_score": round(composite, 2),
                    }
                    if best is None or row["composite_score"] > best["composite_score"]:
                        best = row
                except Exception as e:
                    best = {"symbol": sym, "error": str(e), "composite_score": -9999.0}
            side_results.append(best or {"symbol": sym, "error": "strategy eval failed", "composite_score": -9999.0})

        valid_sides = [x for x in side_results if not x.get("error")]
        if not valid_sides:
            pair_rows.append(
                {
                    "pair": {"long": long_sym, "short": short_sym},
                    "error": "both sides invalid",
                    "sides": side_results,
                }
            )
            continue
        chosen = sorted(valid_sides, key=lambda x: x["composite_score"], reverse=True)[0]
        pair_rows.append(
            {
                "pair": {"long": long_sym, "short": short_sym},
                "sides": side_results,
                "selected_symbol": chosen["symbol"],
                "selected_strategy": chosen["strategy"],
                "selected_score": chosen["composite_score"],
                "selected_metrics": chosen,
            }
        )

    selected = [r for r in pair_rows if r.get("selected_symbol")]
    selected.sort(key=lambda x: x.get("selected_score", -9999.0), reverse=True)
    max_positions = max(1, int(max_total_ratio / max(max_single_ratio, 1e-6)))
    selected = selected[:max_positions]

    if not selected:
        return {
            "market": market,
            "days": days,
            "kline": kline,
            "initial_capital": initial_capital,
            "error": "no selectable pair candidate",
            "selected_pairs": [],
            "pair_details": pair_rows,
            "invalid_symbols": invalid_symbols,
        }

    sleeve_capital = initial_capital * max_single_ratio
    total_alloc = min(initial_capital * max_total_ratio, sleeve_capital * len(selected))
    scale = (total_alloc / (sleeve_capital * len(selected))) if selected else 1.0

    est_weighted_return = 0.0
    est_weighted_dd = 0.0
    for row in selected:
        m = row["selected_metrics"]
        est_weighted_return += float(m.get("total_return_pct", 0.0))
        est_weighted_dd += float(m.get("max_drawdown_pct", 0.0))
    est_weighted_return /= len(selected)
    est_weighted_dd /= len(selected)

    final_capital = initial_capital + total_alloc * (est_weighted_return / 100.0) * scale
    portfolio_return_pct = (final_capital - initial_capital) / initial_capital * 100.0

    return {
        "market": market,
        "days": days,
        "kline": kline,
        "initial_capital": initial_capital,
        "allocated_capital": round(total_alloc, 2),
        "max_single_ratio": max_single_ratio,
        "max_total_ratio": max_total_ratio,
        "selected_pairs": selected,
        "pair_details": pair_rows,
        "invalid_symbols": invalid_symbols,
        "portfolio_estimate": {
            "final_capital": round(final_capital, 2),
            "total_return_pct": round(portfolio_return_pct, 2),
            "avg_selected_return_pct": round(est_weighted_return, 2),
            "avg_selected_max_drawdown_pct": round(est_weighted_dd, 2),
        },
    }

