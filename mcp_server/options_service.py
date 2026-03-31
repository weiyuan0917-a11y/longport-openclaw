from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Literal

from fee_model import estimate_us_option_multi_leg_fee


OptionSide = Literal["buy", "sell"]


@dataclass
class OptionLeg:
    symbol: str
    side: OptionSide
    contracts: int
    price: float = 0.0


def _is_option_symbol(symbol: str) -> bool:
    s = str(symbol or "").upper()
    if ".US" in s and (" C " in s or " P " in s):
        return True
    return bool(re.search(r"\d{6,8}[CP]\d+", s))


def normalize_legs(legs: list[dict[str, Any]]) -> list[OptionLeg]:
    out: list[OptionLeg] = []
    for idx, leg in enumerate(legs or []):
        if not isinstance(leg, dict):
            raise ValueError(f"legs[{idx}] 必须是对象")
        symbol = str(leg.get("symbol", "")).strip().upper()
        side = str(leg.get("side", "")).strip().lower()
        contracts = int(leg.get("contracts", 0))
        price = float(leg.get("price", 0.0) or 0.0)
        if not symbol:
            raise ValueError(f"legs[{idx}].symbol 不能为空")
        if side not in {"buy", "sell"}:
            raise ValueError(f"legs[{idx}].side 仅支持 buy/sell")
        if contracts <= 0:
            raise ValueError(f"legs[{idx}].contracts 必须 > 0")
        if price < 0:
            raise ValueError(f"legs[{idx}].price 不能为负")
        out.append(OptionLeg(symbol=symbol, side=side, contracts=contracts, price=price))
    if not out:
        raise ValueError("legs 不能为空")
    return out


def legs_to_fee_payload(legs: list[OptionLeg]) -> list[dict[str, Any]]:
    return [{"symbol": x.symbol, "side": x.side, "contracts": x.contracts, "price": x.price} for x in legs]


def build_order_legs(
    *,
    legs: list[dict[str, Any]] | None = None,
    symbol: str | None = None,
    side: OptionSide | None = None,
    contracts: int | None = None,
    price: float | None = None,
) -> list[OptionLeg]:
    """Build normalized option legs from either multi-leg payload or single-leg fields."""
    if legs:
        return normalize_legs(legs)
    if not symbol or not side or not contracts:
        raise ValueError("单腿模式需提供 symbol/side/contracts")
    return normalize_legs(
        [
            {
                "symbol": symbol,
                "side": side,
                "contracts": contracts,
                "price": price or 0.0,
            }
        ]
    )


def estimate_option_fee_for_legs(legs: list[OptionLeg]) -> dict[str, Any]:
    return estimate_us_option_multi_leg_fee(legs_to_fee_payload(legs))


def evaluate_option_risk(
    legs: list[OptionLeg],
    available_cash: float,
    max_loss_threshold: float | None = None,
    max_capital_usage: float | None = None,
) -> dict[str, Any]:
    fee = estimate_option_fee_for_legs(legs)
    max_loss_est = float(fee.get("max_loss_estimate", 0.0))
    capital_usage = max(0.0, -float(fee.get("net_premium", 0.0))) + float(fee.get("total_fee", 0.0))

    blocks: list[dict[str, Any]] = []
    if max_loss_threshold is not None and max_loss_est > float(max_loss_threshold):
        blocks.append(
            {
                "rule": "max_loss_threshold",
                "reason": f"策略最大损失估算 {max_loss_est:.2f} 超过阈值 {float(max_loss_threshold):.2f}",
            }
        )
    if max_capital_usage is not None and capital_usage > float(max_capital_usage):
        blocks.append(
            {
                "rule": "capital_usage_limit",
                "reason": f"策略资金占用估算 {capital_usage:.2f} 超过限制 {float(max_capital_usage):.2f}",
            }
        )
    if available_cash >= 0 and capital_usage > available_cash:
        blocks.append(
            {
                "rule": "available_cash",
                "reason": f"可用资金 {available_cash:.2f} 不足以覆盖估算占用 {capital_usage:.2f}",
            }
        )
    return {
        "passed": len(blocks) == 0,
        "blocks": blocks,
        "max_loss_estimate": round(max_loss_est, 6),
        "capital_usage_estimate": round(capital_usage, 6),
        "fee_breakdown": fee.get("fee_breakdown", {}),
    }


def submit_option_order_with_risk(
    trade_ctx: Any,
    legs: list[OptionLeg],
    available_cash: float,
    max_loss_threshold: float | None = None,
    max_capital_usage: float | None = None,
) -> dict[str, Any]:
    risk = evaluate_option_risk(
        legs=legs,
        available_cash=available_cash,
        max_loss_threshold=max_loss_threshold,
        max_capital_usage=max_capital_usage,
    )
    if not risk.get("passed"):
        return {"ok": False, "blocked": True, "risk": risk}

    if len(legs) == 1:
        leg = legs[0]
        order = submit_option_single_leg(trade_ctx, leg.symbol, leg.side, leg.contracts, leg.price or None)
        return {"ok": True, "blocked": False, "mode": "single_leg", "order": order, "risk": risk}

    result = submit_option_multi_leg(trade_ctx, legs)
    if not result.get("ok"):
        return {"ok": False, "blocked": False, "mode": "multi_leg", "result": result, "risk": risk}
    return {"ok": True, "blocked": False, "mode": "multi_leg", "result": result, "risk": risk}


def fetch_option_expiries(quote_ctx: Any, symbol: str) -> dict[str, Any]:
    dates = quote_ctx.option_chain_expiry_date_list(symbol)
    return {"symbol": symbol, "expiries": [d.isoformat() for d in dates]}


def _float_from_quote_field(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _quote_timestamp_iso(v: Any) -> str | None:
    if v is None:
        return None
    try:
        if hasattr(v, "isoformat"):
            return v.isoformat()
    except Exception:
        pass
    return str(v) if v else None


def _fetch_quote_map(quote_ctx: Any, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Batch real-time quotes (LongPort max 500 symbols per request)."""
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        s = str(raw or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        ordered.append(s)
    out: dict[str, dict[str, Any]] = {}
    if not ordered:
        return out
    chunk = 500
    for i in range(0, len(ordered), chunk):
        batch = ordered[i : i + chunk]
        try:
            qs = quote_ctx.quote(batch)
        except Exception:
            continue
        if not qs:
            continue
        for q in qs:
            sym = str(getattr(q, "symbol", "") or "")
            if not sym:
                continue
            out[sym] = {
                "last_done": _float_from_quote_field(getattr(q, "last_done", None)),
                "prev_close": _float_from_quote_field(getattr(q, "prev_close", None)),
                "open": _float_from_quote_field(getattr(q, "open", None)),
                "high": _float_from_quote_field(getattr(q, "high", None)),
                "low": _float_from_quote_field(getattr(q, "low", None)),
                "volume": int(getattr(q, "volume", 0) or 0),
                "timestamp": _quote_timestamp_iso(getattr(q, "timestamp", None)),
            }
    return out


def _attach_option_chain_quotes(rows: list[dict[str, Any]], quote_ctx: Any) -> None:
    syms: list[str] = []
    for row in rows:
        cs = row.get("call_symbol")
        ps = row.get("put_symbol")
        if cs:
            syms.append(str(cs))
        if ps:
            syms.append(str(ps))
    qmap = _fetch_quote_map(quote_ctx, syms)
    for row in rows:
        cs = row.get("call_symbol")
        ps = row.get("put_symbol")
        row["call_quote"] = qmap.get(str(cs)) if cs else None
        row["put_quote"] = qmap.get(str(ps)) if ps else None


def fetch_option_chain(
    quote_ctx: Any,
    symbol: str,
    expiry_date: str | None = None,
    min_strike: float | None = None,
    max_strike: float | None = None,
    standard_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    expiries = quote_ctx.option_chain_expiry_date_list(symbol)
    if not expiries:
        return {"symbol": symbol, "expiries": [], "options": []}
    if expiry_date:
        target = date.fromisoformat(expiry_date)
    else:
        target = min(expiries)
    rows: list[dict[str, Any]] = []
    for item in quote_ctx.option_chain_info_by_date(symbol, target):
        strike = float(item.price) if getattr(item, "price", None) is not None else None
        standard = bool(getattr(item, "standard", False))
        if standard_only and not standard:
            continue
        if min_strike is not None and strike is not None and strike < float(min_strike):
            continue
        if max_strike is not None and strike is not None and strike > float(max_strike):
            continue
        rows.append(
            {
                "expiry_date": target.isoformat(),
                "strike_price": strike,
                "call_symbol": getattr(item, "call_symbol", None),
                "put_symbol": getattr(item, "put_symbol", None),
                "standard": standard,
            }
        )
    rows.sort(key=lambda x: (x["strike_price"] is None, x["strike_price"]))
    total = len(rows)
    lim = max(1, min(int(limit), 500))
    off = max(0, int(offset))
    data = rows[off : off + lim]
    _attach_option_chain_quotes(data, quote_ctx)
    return {
        "symbol": symbol,
        "expiry_date": target.isoformat(),
        "expiries": [d.isoformat() for d in expiries],
        "pagination": {"offset": off, "limit": lim, "total": total, "has_more": (off + lim) < total},
        "options": data,
    }


def submit_option_single_leg(
    trade_ctx: Any,
    symbol: str,
    side: OptionSide,
    contracts: int,
    price: float | None = None,
) -> dict[str, Any]:
    from longport.openapi import OrderSide, OrderType, TimeInForceType

    if contracts <= 0:
        raise ValueError("contracts 必须 > 0")
    resp = trade_ctx.submit_order(
        symbol=symbol,
        order_type=OrderType.LO if price else OrderType.MO,
        side=OrderSide.Buy if side == "buy" else OrderSide.Sell,
        submitted_quantity=contracts,
        time_in_force=TimeInForceType.Day,
        **({} if not price else {"submitted_price": Decimal(str(price))}),
    )
    return {"order_id": resp.order_id, "symbol": symbol, "side": side, "contracts": contracts, "price": price}


def submit_option_multi_leg(
    trade_ctx: Any,
    legs: list[OptionLeg],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for leg in legs:
        try:
            results.append(submit_option_single_leg(trade_ctx, leg.symbol, leg.side, leg.contracts, leg.price or None))
        except Exception as e:
            errors.append({"symbol": leg.symbol, "error": str(e)})
            break
    return {"ok": len(errors) == 0, "legs_submitted": results, "errors": errors}


def get_option_positions(trade_ctx: Any, quote_ctx: Any) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for ch in trade_ctx.stock_positions().channels:
        for pos in ch.positions:
            symbol = str(pos.symbol)
            if not _is_option_symbol(symbol):
                continue
            cur = 0.0
            try:
                q = quote_ctx.quote([symbol])
                if q:
                    cur = float(q[0].last_done)
            except Exception:
                cur = 0.0
            qty = float(pos.quantity)
            cost = float(pos.cost_price)
            pnl = qty * (cur - cost)
            items.append(
                {
                    "symbol": symbol,
                    "quantity": qty,
                    "cost_price": cost,
                    "current_price": cur,
                    "pnl": round(pnl, 2),
                }
            )
    return {"positions": items, "count": len(items)}


def get_option_orders(trade_ctx: Any, status: str = "all") -> dict[str, Any]:
    allowed = {"active": {"New", "PartialFilled"}, "filled": {"Filled"}, "cancelled": {"Canceled"}}.get(status)
    orders: list[dict[str, Any]] = []
    for o in trade_ctx.today_orders():
        symbol = str(o.symbol)
        if not _is_option_symbol(symbol):
            continue
        s = str(o.status)
        if allowed and s not in allowed:
            continue
        orders.append(
            {
                "order_id": o.order_id,
                "symbol": symbol,
                "side": str(o.side),
                "quantity": float(o.quantity),
                "price": float(o.price) if o.price else None,
                "status": s,
            }
        )
    return {"orders": orders, "count": len(orders)}


def run_option_backtest(
    fetch_bars_fn: Callable[[str, int], list[Any]],
    symbol: str,
    template: str,
    days: int = 180,
    holding_days: int = 20,
    contracts: int = 1,
    width_pct: float = 0.05,
) -> dict[str, Any]:
    bars = fetch_bars_fn(symbol, days)
    if len(bars) < max(holding_days + 2, 30):
        raise ValueError("历史数据不足，无法进行期权回测")
    closes = [float(b.close) for b in bars]
    dates = [str(b.date) for b in bars]

    template_key = str(template or "").strip().lower()
    supported = {"bull_call_spread", "bear_put_spread", "straddle", "strangle"}
    if template_key not in supported:
        raise ValueError(f"不支持模板 {template}，可选: {', '.join(sorted(supported))}")

    trade_rows: list[dict[str, Any]] = []
    fee_total = 0.0
    fee_breakdown: dict[str, float] = {}
    step = max(3, int(holding_days))
    qty = max(1, int(contracts))
    width = max(0.01, float(width_pct))

    def _add_fee(parts: dict[str, Any]) -> float:
        fee = float(parts.get("total_fee", 0.0))
        for k, v in (parts.get("fee_breakdown", {}) or {}).items():
            fee_breakdown[k] = fee_breakdown.get(k, 0.0) + float(v)
        return fee

    for i in range(0, len(closes) - step, step):
        s0 = closes[i]
        s1 = closes[i + step]
        d0 = dates[i]
        d1 = dates[i + step]
        gross_per_share = 0.0
        premium_per_share = 0.0
        legs_count = 2

        if template_key == "bull_call_spread":
            k1 = s0
            k2 = s0 * (1 + width)
            premium_per_share = s0 * 0.04
            gross_per_share = max(0.0, s1 - k1) - max(0.0, s1 - k2) - premium_per_share
        elif template_key == "bear_put_spread":
            k1 = s0
            k2 = s0 * (1 - width)
            premium_per_share = s0 * 0.04
            gross_per_share = max(0.0, k1 - s1) - max(0.0, k2 - s1) - premium_per_share
        elif template_key == "straddle":
            premium_per_share = s0 * 0.08
            gross_per_share = abs(s1 - s0) - premium_per_share
        else:  # strangle
            kc = s0 * (1 + width / 2.0)
            kp = s0 * (1 - width / 2.0)
            premium_per_share = s0 * 0.06
            gross_per_share = max(0.0, s1 - kc) + max(0.0, kp - s1) - premium_per_share

        gross = gross_per_share * 100.0 * qty
        fee = _add_fee(
            estimate_us_option_multi_leg_fee(
                [{"side": "buy", "contracts": qty, "price": 0.0} for _ in range(legs_count * 2)]
            )
        )
        fee_total += fee
        net = gross - fee
        trade_rows.append(
            {
                "entry_date": d0,
                "exit_date": d1,
                "entry_spot": round(s0, 4),
                "exit_spot": round(s1, 4),
                "gross_pnl": round(gross, 4),
                "fee": round(fee, 4),
                "net_pnl": round(net, 4),
            }
        )

    total_net = sum(x["net_pnl"] for x in trade_rows)
    wins = [x for x in trade_rows if x["net_pnl"] > 0]
    losses = [x for x in trade_rows if x["net_pnl"] <= 0]
    initial_capital = 100000.0
    total_return_pct = (total_net / initial_capital) * 100.0
    return {
        "symbol": symbol,
        "template": template_key,
        "days": days,
        "holding_days": step,
        "contracts": qty,
        "trades": trade_rows,
        "stats": {
            "total_trades": len(trade_rows),
            "win_rate_pct": round((len(wins) / len(trade_rows) * 100.0) if trade_rows else 0.0, 2),
            "total_net_pnl": round(total_net, 4),
            "total_return_pct": round(total_return_pct, 4),
            "total_fee": round(fee_total, 4),
            "fee_breakdown": {k: round(v, 4) for k, v in fee_breakdown.items()},
            "avg_win": round(sum(x["net_pnl"] for x in wins) / len(wins), 4) if wins else 0.0,
            "avg_loss": round(sum(x["net_pnl"] for x in losses) / len(losses), 4) if losses else 0.0,
        },
    }
