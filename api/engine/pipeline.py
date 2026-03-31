from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .guards import TradeGuard
from .rules_entry import EntryRule
from .rules_exit import ExitRule
from .sizers import PositionSizer
from .types import EntryDecision, ExitDecision, PositionSnapshot, ScanContext


class StrategyPipeline:
    """Composable pipeline: entry + exit + sizer + guard."""

    def __init__(
        self,
        fetch_bars: Any,
        entry_rule: EntryRule,
        exit_rules: list[ExitRule],
        sizer: PositionSizer,
        guards: list[TradeGuard],
    ):
        self._fetch_bars = fetch_bars
        self._entry_rule = entry_rule
        self._exit_rules = sorted(exit_rules, key=lambda x: int(getattr(x, "priority", 100)))
        self._sizer = sizer
        self._guards = guards

    def evaluate_entry(self, ctx: ScanContext) -> EntryDecision:
        return self._entry_rule.evaluate(ctx, self._fetch_bars)

    def evaluate_exit(self, ctx: ScanContext, position: PositionSnapshot) -> ExitDecision:
        traces: list[dict[str, Any]] = []
        for rule in self._exit_rules:
            d = rule.evaluate(ctx, position, self._fetch_bars)
            traces.append(
                {
                    "rule": getattr(rule, "name", d.metadata.get("exit_rule", "unknown")),
                    "hit": bool(d.should_exit),
                    "reason": d.reason,
                    "priority": int(getattr(rule, "priority", d.priority)),
                }
            )
            if d.should_exit:
                d.metadata["exit_trace"] = traces
                return d
        return ExitDecision(False, "no_exit_rule_hit", ctx.strategy_name, 999, {"exit_trace": traces})

    def size_order(
        self,
        symbol: str,
        price: float,
        account: dict[str, Any],
        bars_days: int,
        kline: str,
        config: dict[str, Any],
    ) -> int:
        bars = self._fetch_bars(symbol, bars_days, kline)
        return int(self._sizer.size(symbol, price, account, bars, config))

    def check_guards(
        self,
        symbol: str,
        action: str,
        config: dict[str, Any],
        executed_trades: list[dict[str, Any]],
        positions: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        now = datetime.now()
        for g in self._guards:
            ok, reason = g.check(
                symbol=symbol,
                action=action,
                now=now,
                config=config,
                executed_trades=executed_trades,
                positions=positions,
            )
            if not ok:
                return False, reason or g.name
        return True, ""

    def check_guards_verbose(
        self,
        symbol: str,
        action: str,
        config: dict[str, Any],
        executed_trades: list[dict[str, Any]],
        positions: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        now = datetime.now()
        trace: list[dict[str, Any]] = []
        for g in self._guards:
            ok, reason = g.check(
                symbol=symbol,
                action=action,
                now=now,
                config=config,
                executed_trades=executed_trades,
                positions=positions,
            )
            trace.append(
                {
                    "guard": getattr(g, "name", "unknown_guard"),
                    "passed": bool(ok),
                    "reason": reason or "",
                }
            )
            if not ok:
                return False, reason or getattr(g, "name", "guard_block"), trace
        return True, "", trace

