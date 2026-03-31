from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def build_option_legs_or_400(*, body: Any, build_order_legs: Any) -> list[dict[str, Any]]:
    try:
        return build_order_legs(
            legs=[x.model_dump(exclude_none=True) for x in body.legs] if body.legs else None,
            symbol=body.symbol,
            side=body.side,
            contracts=body.contracts,
            price=body.price,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def build_option_submit_response(submit_result: dict[str, Any]) -> dict[str, Any]:
    if submit_result.get("blocked"):
        raise HTTPException(status_code=400, detail={"error": "option_risk_blocked", "risk": submit_result.get("risk")})
    if not submit_result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "multi_leg_submit_failed",
                "result": submit_result.get("result"),
                "risk": submit_result.get("risk"),
            },
        )
    if submit_result.get("mode") == "single_leg":
        return {"mode": "single_leg", "order": submit_result.get("order"), "risk": submit_result.get("risk")}
    return {"mode": "multi_leg", "result": submit_result.get("result"), "risk": submit_result.get("risk")}

