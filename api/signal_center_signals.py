"""
与「信号中心」页 / 接口 `GET /signals` 一致的指标与信号标志计算（纯函数，便于复用）。
"""
from __future__ import annotations

from typing import Any, Optional


def analyze_signal_center_from_closes(closes: list[float]) -> Optional[dict[str, Any]]:
    """
    输入至少 25 根收盘价序列（时间正序）。
    返回 None 表示数据不足；否则返回 rsi14/ma5/ma20 与 signals 三标志（与 GET /signals 一致）。
    """
    if len(closes) < 25:
        return None
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    rsi = 50.0
    if len(closes) >= 15:
        diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        recent = diffs[-14:]
        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]
        avg_gain = sum(gains) / 14 if gains else 0
        avg_loss = sum(losses) / 14 if losses else 0.0001
        rs = avg_gain / avg_loss
        rsi = 100 - 100 / (1 + rs)
    signal_flags = {
        "rsi_oversold": bool(rsi < 30),
        "ma5_above_ma20": bool(ma5 > ma20),
        "bottom_reversal_hint": bool((rsi < 35 and ma5 >= ma20) or (rsi < 30)),
    }
    return {
        "rsi14": round(float(rsi), 2),
        "ma5": round(float(ma5), 2),
        "ma20": round(float(ma20), 2),
        "signals": signal_flags,
    }
