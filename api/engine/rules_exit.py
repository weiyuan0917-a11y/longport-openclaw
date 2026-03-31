from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from mcp_server.strategies import get_strategy

from .types import ExitDecision, PositionSnapshot, ScanContext


class ExitRule:
    name = "base_exit_rule"
    priority = 100

    def evaluate(self, ctx: ScanContext, position: PositionSnapshot, fetch_bars: Any) -> ExitDecision:
        raise NotImplementedError


class HardStopRule(ExitRule):
    name = "hard_stop"
    priority = 10

    def __init__(self, stop_loss_pct: float = 6.0):
        self.stop_loss_pct = float(stop_loss_pct)

    def evaluate(self, ctx: ScanContext, position: PositionSnapshot, fetch_bars: Any) -> ExitDecision:
        if position.pnl_pct <= -abs(self.stop_loss_pct):
            return ExitDecision(
                should_exit=True,
                reason=f"hard_stop({position.pnl_pct:.2f}%)",
                strategy=ctx.strategy_name,
                priority=self.priority,
                metadata={"exit_rule": self.name},
            )
        return ExitDecision(False, "hard_stop_not_hit", ctx.strategy_name, self.priority, {"exit_rule": self.name})


class TakeProfitRule(ExitRule):
    name = "take_profit"
    priority = 20

    def __init__(self, take_profit_pct: float = 12.0):
        self.take_profit_pct = float(take_profit_pct)

    def evaluate(self, ctx: ScanContext, position: PositionSnapshot, fetch_bars: Any) -> ExitDecision:
        if position.pnl_pct >= abs(self.take_profit_pct):
            return ExitDecision(
                should_exit=True,
                reason=f"take_profit({position.pnl_pct:.2f}%)",
                strategy=ctx.strategy_name,
                priority=self.priority,
                metadata={"exit_rule": self.name},
            )
        return ExitDecision(False, "take_profit_not_hit", ctx.strategy_name, self.priority, {"exit_rule": self.name})


class TimeStopRule(ExitRule):
    name = "time_stop"
    priority = 30

    def __init__(self, max_hold_hours: int = 72):
        self.max_hold_hours = int(max_hold_hours)

    def evaluate(self, ctx: ScanContext, position: PositionSnapshot, fetch_bars: Any) -> ExitDecision:
        if not position.opened_at:
            return ExitDecision(False, "time_stop_no_opened_at", ctx.strategy_name, self.priority, {"exit_rule": self.name})
        if datetime.now() - position.opened_at >= timedelta(hours=self.max_hold_hours):
            return ExitDecision(
                should_exit=True,
                reason=f"time_stop({self.max_hold_hours}h)",
                strategy=ctx.strategy_name,
                priority=self.priority,
                metadata={"exit_rule": self.name},
            )
        return ExitDecision(False, "time_stop_not_hit", ctx.strategy_name, self.priority, {"exit_rule": self.name})


class StrategySellRule(ExitRule):
    name = "strategy_sell"
    priority = 40

    def evaluate(self, ctx: ScanContext, position: PositionSnapshot, fetch_bars: Any) -> ExitDecision:
        bars = fetch_bars(ctx.symbol, ctx.bars_days, ctx.kline)
        if len(bars) < 25:
            return ExitDecision(False, "insufficient_bars", ctx.strategy_name, self.priority, {"exit_rule": self.name})
        sp = ctx.strategy_params if isinstance(ctx.strategy_params, dict) and ctx.strategy_params else None
        sfn = get_strategy(ctx.strategy_name, sp)
        now = sfn(bars, 0)
        if ctx.relaxed_mode:
            hit = now == "sell"
        else:
            prev = sfn(bars[:-1], 0)
            hit = prev != "sell" and now == "sell"
        return ExitDecision(
            should_exit=bool(hit),
            reason="strategy_sell_signal" if hit else "no_sell_signal",
            strategy=ctx.strategy_name,
            priority=self.priority,
            metadata={"exit_rule": self.name},
        )

