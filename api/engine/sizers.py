from __future__ import annotations

import statistics
from typing import Any


class PositionSizer:
    name = "base_sizer"

    def size(
        self,
        symbol: str,
        price: float,
        account: dict[str, Any],
        bars: list[Any],
        config: dict[str, Any],
    ) -> int:
        raise NotImplementedError


class FixedSizer(PositionSizer):
    name = "fixed"

    def __init__(self, quantity: int = 100):
        self.quantity = max(1, int(quantity))

    def size(self, symbol: str, price: float, account: dict[str, Any], bars: list[Any], config: dict[str, Any]) -> int:
        return self.quantity


class RiskPercentSizer(PositionSizer):
    name = "risk_percent"

    def __init__(self, risk_pct: float = 0.01):
        self.risk_pct = max(0.001, float(risk_pct))

    def size(self, symbol: str, price: float, account: dict[str, Any], bars: list[Any], config: dict[str, Any]) -> int:
        net_assets = float(account.get("net_assets") or account.get("total_assets") or account.get("buy_power") or 0.0)
        risk_budget = net_assets * self.risk_pct
        if price <= 0 or risk_budget <= 0:
            return 0
        qty = int(risk_budget / price)
        return max(1, qty)


class VolatilitySizer(PositionSizer):
    name = "volatility"

    def __init__(self, target_vol_pct: float = 0.02):
        self.target_vol_pct = max(0.001, float(target_vol_pct))

    def size(self, symbol: str, price: float, account: dict[str, Any], bars: list[Any], config: dict[str, Any]) -> int:
        if len(bars) < 20 or price <= 0:
            return 0
        closes = [float(x.close) for x in bars[-20:]]
        rets = []
        for i in range(1, len(closes)):
            prev = closes[i - 1]
            cur = closes[i]
            if prev > 0:
                rets.append((cur - prev) / prev)
        if not rets:
            return 0
        vol = statistics.pstdev(rets)
        if vol <= 0:
            return 0
        net_assets = float(account.get("net_assets") or account.get("total_assets") or account.get("buy_power") or 0.0)
        capital = net_assets * self.target_vol_pct / vol
        qty = int(capital / price)
        return max(1, qty)

