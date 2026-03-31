from __future__ import annotations

from typing import Any

from mcp_server.strategies import get_strategy

from .types import EntryDecision, ScanContext


class EntryRule:
    name = "base_entry_rule"

    def evaluate(self, ctx: ScanContext, fetch_bars: Any) -> EntryDecision:
        raise NotImplementedError


class StrategyCrossRule(EntryRule):
    """Use strategy buy signal as entry trigger."""

    name = "strategy_cross"

    def evaluate(self, ctx: ScanContext, fetch_bars: Any) -> EntryDecision:
        bars = fetch_bars(ctx.symbol, ctx.bars_days, ctx.kline)
        if len(bars) < 25:
            return EntryDecision(
                should_enter=False,
                reason="insufficient_bars",
                strategy=ctx.strategy_name,
            )
        sp = ctx.strategy_params if isinstance(ctx.strategy_params, dict) and ctx.strategy_params else None
        sfn = get_strategy(ctx.strategy_name, sp)
        now = sfn(bars, 0)
        if ctx.relaxed_mode:
            hit = now == "buy"
        else:
            prev = sfn(bars[:-1], 0)
            hit = prev != "buy" and now == "buy"
        return EntryDecision(
            should_enter=bool(hit),
            reason="strategy_buy_signal" if hit else "no_buy_signal",
            strategy=ctx.strategy_name,
            metadata={"entry_rule": self.name},
        )


class BreakoutRule(EntryRule):
    """Breakout entry: new high + optional volume confirmation."""

    name = "breakout"

    def __init__(self, lookback_bars: int = 20, min_volume_ratio: float = 1.2):
        self.lookback_bars = max(5, int(lookback_bars))
        self.min_volume_ratio = max(0.0, float(min_volume_ratio))

    def evaluate(self, ctx: ScanContext, fetch_bars: Any) -> EntryDecision:
        bars = fetch_bars(ctx.symbol, ctx.bars_days, ctx.kline)
        need = self.lookback_bars + 1
        if len(bars) < need:
            return EntryDecision(False, "insufficient_bars", ctx.strategy_name, metadata={"entry_rule": self.name})
        last = bars[-1]
        prev = bars[-(self.lookback_bars + 1):-1]
        last_close = float(last.close)
        prev_high = max(float(x.high) for x in prev) if prev else float(last.high)
        breakout_hit = last_close > prev_high

        avg_vol = 0.0
        vol_ratio = 0.0
        if prev:
            vols = [float(x.volume) for x in prev]
            avg_vol = sum(vols) / len(vols) if vols else 0.0
            if avg_vol > 0:
                vol_ratio = float(last.volume) / avg_vol
        vol_ok = vol_ratio >= self.min_volume_ratio if self.min_volume_ratio > 0 else True
        hit = breakout_hit and vol_ok
        reason = "breakout_confirmed" if hit else ("breakout_no_volume" if breakout_hit else "no_breakout")
        return EntryDecision(
            should_enter=bool(hit),
            reason=reason,
            strategy=ctx.strategy_name,
            metadata={
                "entry_rule": self.name,
                "lookback_bars": self.lookback_bars,
                "prev_high": round(prev_high, 6),
                "last_close": round(last_close, 6),
                "volume_ratio": round(vol_ratio, 3),
            },
        )


class MeanReversionRule(EntryRule):
    """Mean reversion entry: RSI oversold + MA deviation."""

    name = "mean_reversion"

    def __init__(self, rsi_threshold: float = 35.0, ma_period: int = 20, deviation_pct: float = 2.0):
        self.rsi_threshold = float(rsi_threshold)
        self.ma_period = max(5, int(ma_period))
        self.deviation_pct = max(0.0, float(deviation_pct))

    def _rsi14(self, closes: list[float]) -> float:
        if len(closes) < 15:
            return 50.0
        diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        recent = diffs[-14:]
        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]
        avg_gain = sum(gains) / 14 if gains else 0.0
        avg_loss = sum(losses) / 14 if losses else 0.0001
        rs = avg_gain / avg_loss if avg_loss > 0 else 0.0
        return 100 - 100 / (1 + rs) if rs >= 0 else 50.0

    def evaluate(self, ctx: ScanContext, fetch_bars: Any) -> EntryDecision:
        bars = fetch_bars(ctx.symbol, ctx.bars_days, ctx.kline)
        need = max(25, self.ma_period + 1)
        if len(bars) < need:
            return EntryDecision(False, "insufficient_bars", ctx.strategy_name, metadata={"entry_rule": self.name})
        closes = [float(x.close) for x in bars]
        ma = sum(closes[-self.ma_period:]) / self.ma_period
        last = closes[-1]
        dev_pct = ((ma - last) / ma * 100.0) if ma > 0 else 0.0
        rsi = self._rsi14(closes)
        hit = (rsi <= self.rsi_threshold) and (dev_pct >= self.deviation_pct)
        reason = "mean_reversion_signal" if hit else "no_mean_reversion_signal"
        return EntryDecision(
            should_enter=bool(hit),
            reason=reason,
            strategy=ctx.strategy_name,
            metadata={
                "entry_rule": self.name,
                "rsi14": round(rsi, 3),
                "rsi_threshold": self.rsi_threshold,
                "ma_period": self.ma_period,
                "deviation_pct": round(dev_pct, 3),
                "deviation_threshold": self.deviation_pct,
            },
        )

