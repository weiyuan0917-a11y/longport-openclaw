from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from api import runtime_bridge as rt

router = APIRouter(tags=["options-trade"])


@router.get("/trade/account")
def trade_account() -> dict[str, Any]:
    return rt.trade_account()


@router.get("/options/expiries")
def options_expiries(symbol: str) -> dict[str, Any]:
    return rt.options_expiries(symbol=symbol)


@router.get("/options/chain")
def options_chain(
    symbol: str,
    expiry_date: str | None = None,
    min_strike: float | None = None,
    max_strike: float | None = None,
    standard_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    return rt.options_chain(
        symbol=symbol,
        expiry_date=expiry_date,
        min_strike=min_strike,
        max_strike=max_strike,
        standard_only=standard_only,
        limit=limit,
        offset=offset,
    )


@router.post("/options/fee-estimate")
def options_fee_estimate(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.options_fee_estimate(body)


@router.post("/options/order")
def options_order(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.options_order(body)


@router.get("/options/orders")
def options_orders(status: str = "all") -> dict[str, Any]:
    return rt.options_orders(status=status)


@router.get("/options/positions")
def options_positions() -> dict[str, Any]:
    return rt.options_positions()


@router.post("/options/backtest")
def options_backtest(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.options_backtest(body)


@router.get("/trade/positions")
def trade_positions() -> dict[str, Any]:
    return rt.trade_positions()


@router.get("/trade/orders")
def trade_orders(status: str = "all") -> dict[str, Any]:
    return rt.trade_orders(status=status)


@router.post("/trade/order")
def trade_submit_order(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.trade_submit_order(body)


@router.post("/trade/order/{order_id}/cancel")
def trade_cancel_order(order_id: str) -> dict[str, Any]:
    return rt.trade_cancel_order(order_id=order_id)

