from __future__ import annotations

import copy
from typing import Any, Literal


OrderSide = Literal["buy", "sell"]
StockMarket = Literal["HK", "US", "CN", "OTHER"]


DEFAULT_FEE_SCHEDULE: dict[str, Any] = {
    "version": "1.0",
    "hk_stock": {
        "commission": {"enabled": False, "rate_pct": 0.03, "min_per_order": 3.1},
        "platform_fee": {"type": "fixed_per_order", "amount": 15.0},
        "stamp_duty": {"type": "pct_of_notional", "rate_pct": 0.1, "applies_to": "both"},
        "trading_fee": {"type": "pct_of_notional", "rate_pct": 0.00565, "min_per_order": 0.01},
        "sfc_levy": {"type": "pct_of_notional", "rate_pct": 0.0027, "min_per_order": 0.01},
        "afrc_levy": {"type": "pct_of_notional", "rate_pct": 0.00015, "min_per_order": 0.01},
        "ccass_fee": {"type": "pct_of_notional", "rate_pct": 0.0042},
    },
    "us_stock": {
        "platform_fee": {
            "type": "per_share",
            "amount_per_share": 0.005,
            "min_per_order": 1.0,
            "max_pct_of_notional": 0.99,
        },
        "settlement_fee": {"type": "per_share", "amount_per_share": 0.003, "max_pct_of_notional": 7.0},
        "sec_fee": {"type": "per_order", "amount": 0.0, "applies_to": "sell"},
        "taf": {
            "type": "per_share",
            "amount_per_share": 0.000195,
            "min_per_order": 0.01,
            "max_per_order": 9.79,
            "applies_to": "sell",
        },
    },
    "us_option_regular": {
        "commission": {"type": "per_contract", "amount_per_contract": 0.45, "min_per_order": 1.49, "applies_to": "both"},
        "platform_fee": {"type": "per_contract", "amount_per_contract": 0.3, "min_per_order": 0.3, "applies_to": "both"},
        "option_settlement_fee": {"type": "per_contract", "amount_per_contract": 0.18, "applies_to": "both"},
        "option_regulatory_fee": {"type": "per_contract", "amount_per_contract": 0.02295, "applies_to": "both"},
        "option_clearing_fee": {"type": "per_contract", "amount_per_contract": 0.025, "applies_to": "both"},
        "option_taf": {"type": "per_contract", "amount_per_contract": 0.00329, "min_per_order": 0.01, "applies_to": "sell"},
        "exercise_assignment_fee": {"type": "unknown", "amount": None},
    },
}

_RUNTIME_FEE_SCHEDULE: dict[str, Any] = copy.deepcopy(DEFAULT_FEE_SCHEDULE)


def _deep_merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        out = {k: copy.deepcopy(v) for k, v in base.items()}
        for k, v in patch.items():
            if k in out:
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out
    return copy.deepcopy(patch)


def set_fee_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    global _RUNTIME_FEE_SCHEDULE
    if not isinstance(schedule, dict):
        raise ValueError("fee schedule 必须是 JSON object")
    merged = _deep_merge(DEFAULT_FEE_SCHEDULE, schedule)
    _RUNTIME_FEE_SCHEDULE = merged
    return get_fee_schedule()


def get_fee_schedule() -> dict[str, Any]:
    return copy.deepcopy(_RUNTIME_FEE_SCHEDULE)


def get_default_fee_schedule() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_FEE_SCHEDULE)


def _apply_side(rule: dict[str, Any], side: OrderSide) -> bool:
    applies = str(rule.get("applies_to", "both")).lower()
    return applies in {"both", side}


def _clip_min_max(value: float, rule: dict[str, Any], notional: float) -> float:
    x = float(value)
    if "min_per_order" in rule:
        x = max(x, float(rule["min_per_order"]))
    if "max_per_order" in rule:
        x = min(x, float(rule["max_per_order"]))
    if "max_pct_of_notional" in rule and notional > 0:
        x = min(x, notional * float(rule["max_pct_of_notional"]) / 100.0)
    return max(0.0, x)


def estimate_stock_order_fee(market: StockMarket, side: OrderSide, quantity: int, price: float) -> dict[str, Any]:
    qty = max(0, int(quantity))
    px = max(0.0, float(price))
    notional = qty * px
    fee_components: dict[str, float] = {}
    stamp_duty = 0.0

    schedule = _RUNTIME_FEE_SCHEDULE
    if market == "HK":
        cfg = schedule["hk_stock"]
        commission = cfg["commission"]
        if commission.get("enabled", False):
            comm = notional * float(commission.get("rate_pct", 0.0)) / 100.0
            comm = _clip_min_max(comm, commission, notional)
            fee_components["commission"] = round(comm, 6)
        pf = float(cfg["platform_fee"].get("amount", 0.0))
        fee_components["platform_fee"] = round(max(0.0, pf), 6)
        for k in ("trading_fee", "sfc_levy", "afrc_levy", "ccass_fee"):
            rule = cfg[k]
            v = notional * float(rule.get("rate_pct", 0.0)) / 100.0
            v = _clip_min_max(v, rule, notional)
            fee_components[k] = round(v, 6)
        stamp_rule = cfg["stamp_duty"]
        if _apply_side(stamp_rule, side):
            stamp_duty = notional * float(stamp_rule.get("rate_pct", 0.0)) / 100.0
            stamp_duty = _clip_min_max(stamp_duty, stamp_rule, notional)
    elif market == "US":
        cfg = schedule["us_stock"]
        for k in ("platform_fee", "settlement_fee", "taf"):
            rule = cfg[k]
            if not _apply_side(rule, side):
                continue
            v = qty * float(rule.get("amount_per_share", 0.0))
            v = _clip_min_max(v, rule, notional)
            fee_components[k] = round(v, 6)
        sec = cfg["sec_fee"]
        if _apply_side(sec, side):
            fee_components["sec_fee"] = round(max(0.0, float(sec.get("amount", 0.0))), 6)

    total_commission_like = float(sum(fee_components.values()))
    total_fee = total_commission_like + float(stamp_duty)
    return {
        "market": market,
        "side": side,
        "quantity": qty,
        "price": round(px, 6),
        "notional": round(notional, 6),
        "components": fee_components,
        "stamp_duty": round(float(stamp_duty), 6),
        "commission_like": round(total_commission_like, 6),
        "total_fee": round(total_fee, 6),
    }


def estimate_us_option_order_fee(side: OrderSide, contracts: int) -> dict[str, Any]:
    qty = max(0, int(contracts))
    cfg = _RUNTIME_FEE_SCHEDULE["us_option_regular"]
    components: dict[str, float] = {}
    for k in (
        "commission",
        "platform_fee",
        "option_settlement_fee",
        "option_regulatory_fee",
        "option_clearing_fee",
        "option_taf",
    ):
        rule = cfg[k]
        if not _apply_side(rule, side):
            continue
        per = float(rule.get("amount_per_contract", 0.0))
        v = qty * per
        v = _clip_min_max(v, rule, notional=0.0)
        components[k] = round(v, 6)
    total = float(sum(components.values()))
    return {
        "market": "US_OPTION",
        "side": side,
        "contracts": qty,
        "components": components,
        "total_fee": round(total, 6),
    }


def estimate_us_option_multi_leg_fee(legs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    聚合多腿期权费用并输出 fee_breakdown。
    legs 示例:
      [{"side": "buy", "contracts": 1, "price": 1.2, "symbol": "AAPL..."}, ...]
    """
    if not isinstance(legs, list) or not legs:
        raise ValueError("legs 不能为空")

    leg_details: list[dict[str, Any]] = []
    fee_breakdown: dict[str, float] = {}
    net_premium = 0.0
    total_fee = 0.0
    total_contracts = 0

    for idx, leg in enumerate(legs):
        if not isinstance(leg, dict):
            raise ValueError(f"legs[{idx}] 必须是对象")
        side_raw = str(leg.get("side", "")).strip().lower()
        if side_raw not in {"buy", "sell"}:
            raise ValueError(f"legs[{idx}].side 仅支持 buy/sell")
        contracts = int(leg.get("contracts", 0))
        if contracts <= 0:
            raise ValueError(f"legs[{idx}].contracts 必须 > 0")
        price = float(leg.get("price", 0.0) or 0.0)
        symbol = str(leg.get("symbol", "")).strip() or None

        est = estimate_us_option_order_fee(side=side_raw, contracts=contracts)
        comp = {k: float(v) for k, v in (est.get("components") or {}).items()}
        fee = float(est.get("total_fee", 0.0))
        total_fee += fee
        total_contracts += contracts
        sign = -1.0 if side_raw == "buy" else 1.0
        premium_cashflow = sign * contracts * price * 100.0
        net_premium += premium_cashflow

        for k, v in comp.items():
            fee_breakdown[k] = fee_breakdown.get(k, 0.0) + float(v)

        leg_details.append(
            {
                "symbol": symbol,
                "side": side_raw,
                "contracts": contracts,
                "price": round(price, 6),
                "premium_cashflow": round(premium_cashflow, 6),
                "fee": round(fee, 6),
                "fee_breakdown": {k: round(v, 6) for k, v in comp.items()},
            }
        )

    return {
        "market": "US_OPTION",
        "legs": leg_details,
        "contracts_total": total_contracts,
        "net_premium": round(net_premium, 6),
        "total_fee": round(total_fee, 6),
        "fee_breakdown": {k: round(v, 6) for k, v in fee_breakdown.items()},
        "max_loss_estimate": round(max(0.0, -net_premium) + total_fee, 6),
    }
