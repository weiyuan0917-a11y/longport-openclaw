from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api import runtime_bridge as rt

router = APIRouter(tags=["dashboard-market"])


@router.get("/dashboard/summary")
def dashboard_summary() -> dict[str, Any]:
    return rt.dashboard_summary()


@router.get("/market/analysis")
def market_analysis() -> dict[str, Any]:
    return rt.market_analysis()


@router.get("/market/sectors")
def market_sectors(days: int = 5) -> dict[str, Any]:
    return rt.market_sectors(days=days)


@router.get("/signals")
def signals(symbol: str = "RXRX.US") -> dict[str, Any]:
    return rt.signals(symbol=symbol)

