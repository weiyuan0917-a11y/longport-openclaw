from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class EntryDecision:
    should_enter: bool
    reason: str
    strategy: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExitDecision:
    should_exit: bool
    reason: str
    strategy: str = ""
    priority: int = 100
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PositionSnapshot:
    symbol: str
    quantity: int
    avg_cost: float
    current_price: float
    opened_at: Optional[datetime] = None

    @property
    def pnl_pct(self) -> float:
        if self.avg_cost <= 0:
            return 0.0
        return (self.current_price - self.avg_cost) / self.avg_cost * 100.0


@dataclass
class MarketSnapshot:
    symbol: str
    bars: list[Any]
    quote: dict[str, Any]


@dataclass
class ScanContext:
    symbol: str
    strategy_name: str
    bars_days: int
    kline: str
    relaxed_mode: bool
    market_snapshot: Optional[MarketSnapshot] = None
    extra: dict[str, Any] = field(default_factory=dict)
    # 与 score_strategies 矩阵变体一致，传入 get_strategy(name, params)
    strategy_params: Optional[dict[str, Any]] = None

