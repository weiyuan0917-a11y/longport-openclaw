from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body

from api import runtime_bridge as rt

router = APIRouter(tags=["fees-risk"])


@router.get("/fees/schedule")
def fees_schedule() -> dict[str, Any]:
    return rt.fees_schedule()


@router.get("/fees/schedule/default")
def fees_schedule_default() -> dict[str, Any]:
    return rt.fees_schedule_default()


@router.post("/fees/schedule")
def fees_schedule_save(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return rt.fees_schedule_save(body)


@router.get("/fees/estimate")
def fees_estimate(
    asset_class: Literal["stock", "us_option"] = "stock",
    market: Literal["HK", "US", "CN", "OTHER"] = "US",
    side: Literal["buy", "sell"] = "buy",
    quantity: int = 100,
    price: float = 1.0,
) -> dict[str, Any]:
    return rt.fees_estimate(
        asset_class=asset_class,
        market=market,
        side=side,
        quantity=quantity,
        price=price,
    )


@router.get("/risk/config")
def risk_config() -> dict[str, Any]:
    return rt.risk_config()

