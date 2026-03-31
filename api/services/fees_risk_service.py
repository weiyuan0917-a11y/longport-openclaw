from __future__ import annotations

from typing import Any, Callable, Literal

from fastapi import HTTPException


def build_fee_schedule_response(schedule: dict[str, Any]) -> dict[str, Any]:
    return {"version": str(schedule.get("version", "1.0")), "schedule": schedule}


def save_fee_schedule(
    *,
    schedule_payload: dict[str, Any],
    set_fee_schedule: Callable[[dict[str, Any]], dict[str, Any]],
    save_fee_schedule_file: Callable[[str, dict[str, Any]], None],
    fee_schedule_file: str,
) -> dict[str, Any]:
    try:
        updated = set_fee_schedule(schedule_payload)
        save_fee_schedule_file(fee_schedule_file, updated)
        return {"ok": True, "version": str(updated.get("version", "1.0")), "schedule": updated}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"费用配置格式错误: {e}")


def estimate_fees(
    *,
    asset_class: Literal["stock", "us_option"],
    market: Literal["HK", "US", "CN", "OTHER"],
    side: Literal["buy", "sell"],
    quantity: int,
    price: float,
    estimate_stock_order_fee: Callable[..., dict[str, Any]],
    estimate_us_option_order_fee: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity 必须 > 0")
    if price < 0:
        raise HTTPException(status_code=400, detail="price 不能为负")
    if asset_class == "stock":
        est = estimate_stock_order_fee(market=market, side=side, quantity=quantity, price=price)
        return {"asset_class": asset_class, "estimate": est}
    est_opt = estimate_us_option_order_fee(side=side, contracts=quantity)
    return {"asset_class": asset_class, "estimate": est_opt}


def build_risk_config_response(*, load_config: Callable[[], Any]) -> dict[str, Any]:
    return load_config().to_dict()

