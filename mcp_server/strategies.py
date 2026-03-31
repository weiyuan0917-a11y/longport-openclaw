"""
strategies.py - 内置回测策略
每个策略都是 (bars: list[Bar], position: int) -> "buy" | "sell" | "hold"
"""
from __future__ import annotations
import inspect
from typing import Any
from backtest_engine import Bar


# ============================================================
# 工具函数
# ============================================================

def _sma(bars: list[Bar], n: int) -> float | None:
    """简单移动平均"""
    if len(bars) < n:
        return None
    return sum(b.close for b in bars[-n:]) / n


def _ema(closes: list[float], n: int) -> list[float]:
    """指数移动平均（全序列）"""
    if len(closes) < n:
        return []
    k   = 2 / (n + 1)
    ema = [sum(closes[:n]) / n]
    for c in closes[n:]:
        ema.append(c * k + ema[-1] * (1 - k))
    return ema


def _rsi(bars: list[Bar], n: int = 14) -> float | None:
    """RSI"""
    if len(bars) < n + 1:
        return None
    closes = [b.close for b in bars[-(n+1):]]
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains) / n
    avg_l = sum(losses) / n
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def _atr_list(bars: list[Bar], n: int = 14) -> list[float]:
    """ATR 序列（简单滑窗）"""
    if len(bars) < 2:
        return []
    trs: list[float] = [0.0]
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    out: list[float] = []
    for i in range(len(trs)):
        if i < n:
            out.append(0.0)
        else:
            w = trs[i - n + 1:i + 1]
            out.append(sum(w) / n)
    return out


def _adx(bars: list[Bar], n: int = 14) -> float | None:
    """ADX（简化实现）"""
    if len(bars) < n * 2 + 2:
        return None
    plus_dm = [0.0]
    minus_dm = [0.0]
    tr = [0.0]
    for i in range(1, len(bars)):
        up = bars[i].high - bars[i - 1].high
        down = bars[i - 1].low - bars[i].low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - bars[i - 1].close),
            abs(bars[i].low - bars[i - 1].close),
        ))
    dx_list: list[float] = []
    for i in range(n, len(bars)):
        tr_n = sum(tr[i - n + 1:i + 1])
        pdm_n = sum(plus_dm[i - n + 1:i + 1])
        mdm_n = sum(minus_dm[i - n + 1:i + 1])
        if tr_n <= 0:
            dx_list.append(0.0)
            continue
        pdi = 100 * pdm_n / tr_n
        mdi = 100 * mdm_n / tr_n
        den = pdi + mdi
        dx = 100 * abs(pdi - mdi) / den if den > 0 else 0.0
        dx_list.append(dx)
    if len(dx_list) < n:
        return None
    return sum(dx_list[-n:]) / n


def _supertrend_series(bars: list[Bar], period: int = 10, multiplier: float = 3.0) -> list[float]:
    """SuperTrend 序列（简化版）"""
    if len(bars) < period + 2:
        return []
    atr = _atr_list(bars, period)
    hl2 = [(b.high + b.low) / 2 for b in bars]
    upper = [hl2[i] + multiplier * atr[i] for i in range(len(bars))]
    lower = [hl2[i] - multiplier * atr[i] for i in range(len(bars))]
    f_upper = upper[:]
    f_lower = lower[:]
    st = [0.0] * len(bars)
    st[0] = upper[0]
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        f_upper[i] = upper[i] if (upper[i] < f_upper[i - 1] or prev_close > f_upper[i - 1]) else f_upper[i - 1]
        f_lower[i] = lower[i] if (lower[i] > f_lower[i - 1] or prev_close < f_lower[i - 1]) else f_lower[i - 1]
        if st[i - 1] == f_upper[i - 1]:
            st[i] = f_upper[i] if bars[i].close <= f_upper[i] else f_lower[i]
        else:
            st[i] = f_lower[i] if bars[i].close >= f_lower[i] else f_upper[i]
    return st


# ============================================================
# 策略 1：双均线交叉（MA Cross）
# ============================================================

def make_ma_cross(fast: int = 5, slow: int = 20):
    """
    信号：
      买入：MA_fast 上穿 MA_slow
      卖出：MA_fast 下穿 MA_slow
    """
    def strategy(bars: list[Bar], position: int) -> str:
        if len(bars) < slow + 1:
            return "hold"
        ma_fast_now  = _sma(bars,      fast)
        ma_slow_now  = _sma(bars,      slow)
        ma_fast_prev = _sma(bars[:-1], fast)
        ma_slow_prev = _sma(bars[:-1], slow)
        if None in (ma_fast_now, ma_slow_now, ma_fast_prev, ma_slow_prev):
            return "hold"
        # 金叉
        if ma_fast_prev <= ma_slow_prev and ma_fast_now > ma_slow_now:
            return "buy"
        # 死叉
        if ma_fast_prev >= ma_slow_prev and ma_fast_now < ma_slow_now:
            return "sell"
        return "hold"

    strategy.__name__ = f"MA_Cross({fast},{slow})"
    return strategy


# ============================================================
# 策略 2：RSI 超买超卖
# ============================================================

def make_rsi_strategy(period: int = 14, oversold: float = 30, overbought: float = 70):
    """
    信号：
      买入：RSI 从超卖区（<oversold）回升穿过 oversold
      卖出：RSI 从超买区（>overbought）回落穿过 overbought
    """
    def strategy(bars: list[Bar], position: int) -> str:
        if len(bars) < period + 2:
            return "hold"
        rsi_now  = _rsi(bars,      period)
        rsi_prev = _rsi(bars[:-1], period)
        if rsi_now is None or rsi_prev is None:
            return "hold"
        # 超卖反弹
        if rsi_prev < oversold and rsi_now >= oversold and position == 0:
            return "buy"
        # 超买回落
        if rsi_prev > overbought and rsi_now <= overbought and position > 0:
            return "sell"
        return "hold"

    strategy.__name__ = f"RSI({period},{oversold},{overbought})"
    return strategy


# ============================================================
# 策略 3：MACD 金叉/死叉
# ============================================================

def make_macd_strategy(fast: int = 12, slow: int = 26, signal: int = 9):
    """
    信号：
      买入：MACD 线上穿信号线（金叉）
      卖出：MACD 线下穿信号线（死叉）
    """
    def strategy(bars: list[Bar], position: int) -> str:
        if len(bars) < slow + signal + 1:
            return "hold"
        closes   = [b.close for b in bars]
        ema_fast = _ema(closes, fast)
        ema_slow = _ema(closes, slow)
        if len(ema_fast) < len(ema_slow):
            return "hold"
        # 对齐
        offset   = len(ema_fast) - len(ema_slow)
        macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
        if len(macd_line) < signal + 1:
            return "hold"
        sig_line = _ema(macd_line, signal)
        if len(sig_line) < 2:
            return "hold"
        macd_now   = macd_line[-1]
        macd_prev  = macd_line[-2]
        sig_now    = sig_line[-1]
        sig_prev   = sig_line[-2]
        # 金叉
        if macd_prev <= sig_prev and macd_now > sig_now:
            return "buy"
        # 死叉
        if macd_prev >= sig_prev and macd_now < sig_now:
            return "sell"
        return "hold"

    strategy.__name__ = f"MACD({fast},{slow},{signal})"
    return strategy


# ============================================================
# 策略 4：布林带突破
# ============================================================

def make_bollinger_strategy(period: int = 20, std_dev: float = 2.0):
    """
    信号：
      买入：价格从下轨反弹穿过下轨（超卖反弹）
      卖出：价格从上轨回落穿过上轨（超买回落）
            或持仓时价格跌破中轨（趋势转弱）
    """
    def _bands(bars: list[Bar]):
        if len(bars) < period:
            return None, None, None
        closes = [b.close for b in bars[-period:]]
        mid    = sum(closes) / period
        var    = sum((c - mid)**2 for c in closes) / period
        std    = var ** 0.5
        return mid - std_dev * std, mid, mid + std_dev * std

    def strategy(bars: list[Bar], position: int) -> str:
        if len(bars) < period + 1:
            return "hold"
        lower_now,  mid_now,  upper_now  = _bands(bars)
        lower_prev, mid_prev, upper_prev = _bands(bars[:-1])
        if None in (lower_now, lower_prev):
            return "hold"
        cur  = bars[-1].close
        prev = bars[-2].close
        # 下轨反弹入场
        if prev < lower_prev and cur >= lower_now and position == 0:
            return "buy"
        # 上轨回落平仓
        if prev > upper_prev and cur <= upper_now and position > 0:
            return "sell"
        # 跌破中轨止盈
        if cur < mid_now and position > 0:
            return "sell"
        return "hold"

    strategy.__name__ = f"Bollinger({period},{std_dev})"
    return strategy


# ============================================================
# 策略 5：北冥有鱼（犹豫区 + T线突破，long 版）
# ============================================================

def make_beiming_strategy(
    overlap: int = 5,
    oscillation_ratio: float = 0.05,
    breakout_body_min: float = 0.8,
    breakout_shadow_max: float = 0.1,
    stop_loss_ratio: float = 0.01,
    profit_loss_ratio: float = 9.0,
):
    """
    规则（适配当前 long-only 回测引擎）：
      1) 用最近 overlap 根（不含当前）识别“犹豫区”（高低振幅/中位价 <= oscillation_ratio）
      2) 当前 K 线满足强突破（实体占比大、影线占比小）且向上突破压力 T 线 -> 买入
      3) 持仓后满足任一条件卖出：
         - 跌破止损（T 线下方 stop_loss_ratio）
         - 达到目标盈亏比（risk * profit_loss_ratio）
         - 出现反向犹豫（价格回到新的压力区下方）
    """
    state = {
        "stop_loss": None,
        "target_profit": None,
    }

    def strategy(bars: list[Bar], position: int) -> str:
        if len(bars) < overlap + 2:
            return "hold"

        cur = bars[-1]
        prev = bars[-2]

        # 用“前 overlap 根”识别犹豫区，避免未来函数
        window = bars[-(overlap + 1):-1]
        window_high = max(b.high for b in window)
        window_low = min(b.low for b in window)
        window_mid = (window_high + window_low) / 2 if (window_high + window_low) != 0 else 0.0
        is_hesitation = False
        if window_mid > 0:
            is_hesitation = ((window_high - window_low) / window_mid) <= oscillation_ratio

        # T线判定：更接近低点 -> 支撑；更接近高点 -> 压力
        t_line = None
        t_type = ""
        if is_hesitation:
            if abs(prev.close - window_low) < abs(prev.close - window_high):
                t_line = window_low
                t_type = "support"
            else:
                t_line = window_high
                t_type = "resistance"

        # 当前 K 线形态质量
        total_range = max(cur.high - cur.low, 1e-9)
        body = abs(cur.close - cur.open)
        upper_shadow = cur.high - max(cur.close, cur.open)
        lower_shadow = min(cur.close, cur.open) - cur.low
        body_ratio = body / total_range
        shadow_ratio = (upper_shadow + lower_shadow) / total_range

        # 开仓：犹豫区后的向上强突破
        if position == 0:
            if (
                is_hesitation
                and t_type == "resistance"
                and t_line is not None
                and cur.close > t_line
                and cur.close > cur.open
                and body_ratio >= breakout_body_min
                and shadow_ratio <= breakout_shadow_max
            ):
                entry = cur.close
                stop_loss = t_line * (1 - stop_loss_ratio)
                risk = max(entry - stop_loss, entry * 0.001)
                target_profit = entry + risk * profit_loss_ratio
                state["stop_loss"] = stop_loss
                state["target_profit"] = target_profit
                return "buy"
            return "hold"

        # 平仓：止损 / 止盈 / 反向犹豫
        stop_loss = state.get("stop_loss")
        target_profit = state.get("target_profit")
        if stop_loss is not None and cur.close <= stop_loss:
            state["stop_loss"] = None
            state["target_profit"] = None
            return "sell"
        if target_profit is not None and cur.close >= target_profit:
            state["stop_loss"] = None
            state["target_profit"] = None
            return "sell"

        # 动态止盈：新形成的压力犹豫区下方走弱，先行离场
        if is_hesitation and t_type == "resistance" and cur.close < t_line:
            state["stop_loss"] = None
            state["target_profit"] = None
            return "sell"

        return "hold"

    strategy.__name__ = f"BeiMingYu({overlap},{oscillation_ratio})"
    return strategy


# ============================================================
# 策略 6：Donchian Breakout（海龟突破）
# ============================================================

def make_donchian_breakout(entry_period: int = 20, exit_period: int = 10):
    """
    信号：
      买入：收盘价突破前 entry_period 根最高价
      卖出：收盘价跌破前 exit_period 根最低价
    """
    def strategy(bars: list[Bar], position: int) -> str:
        need = max(entry_period, exit_period) + 2
        if len(bars) < need:
            return "hold"
        cur = bars[-1].close
        entry_high = max(b.high for b in bars[-(entry_period + 1):-1])
        exit_low = min(b.low for b in bars[-(exit_period + 1):-1])
        if position == 0 and cur > entry_high:
            return "buy"
        if position > 0 and cur < exit_low:
            return "sell"
        return "hold"

    strategy.__name__ = f"Donchian({entry_period},{exit_period})"
    return strategy


# ============================================================
# 策略 7：SuperTrend 趋势跟随
# ============================================================

def make_supertrend_strategy(period: int = 10, multiplier: float = 3.0):
    """
    信号：
      买入：收盘价上穿 SuperTrend 线
      卖出：收盘价下穿 SuperTrend 线
    """
    def strategy(bars: list[Bar], position: int) -> str:
        st = _supertrend_series(bars, period=period, multiplier=multiplier)
        if len(st) < 2:
            return "hold"
        prev_close = bars[-2].close
        cur_close = bars[-1].close
        prev_st = st[-2]
        cur_st = st[-1]
        if position == 0 and prev_close <= prev_st and cur_close > cur_st:
            return "buy"
        if position > 0 and prev_close >= prev_st and cur_close < cur_st:
            return "sell"
        return "hold"

    strategy.__name__ = f"SuperTrend({period},{multiplier})"
    return strategy


# ============================================================
# 策略 8：ADX 过滤的双均线
# ============================================================

def make_adx_ma_filter(
    fast: int = 10,
    slow: int = 30,
    adx_period: int = 14,
    adx_threshold: float = 20.0,
):
    """
    信号：
      买入：均线金叉且 ADX >= 阈值
      卖出：均线死叉
    """
    def strategy(bars: list[Bar], position: int) -> str:
        if len(bars) < max(slow + 2, adx_period * 2 + 2):
            return "hold"
        ma_fast_now = _sma(bars, fast)
        ma_slow_now = _sma(bars, slow)
        ma_fast_prev = _sma(bars[:-1], fast)
        ma_slow_prev = _sma(bars[:-1], slow)
        adx_now = _adx(bars, adx_period)
        if None in (ma_fast_now, ma_slow_now, ma_fast_prev, ma_slow_prev, adx_now):
            return "hold"
        if position == 0 and ma_fast_prev <= ma_slow_prev and ma_fast_now > ma_slow_now and adx_now >= adx_threshold:
            return "buy"
        if position > 0 and ma_fast_prev >= ma_slow_prev and ma_fast_now < ma_slow_now:
            return "sell"
        return "hold"

    strategy.__name__ = f"ADX_MA({fast},{slow},{adx_period},{adx_threshold})"
    return strategy


# ============================================================
# 策略注册表
# ============================================================

STRATEGY_REGISTRY = {
    "ma_cross":   make_ma_cross,
    "rsi":        make_rsi_strategy,
    "macd":       make_macd_strategy,
    "bollinger":  make_bollinger_strategy,
    "beiming":    make_beiming_strategy,
    "donchian_breakout": make_donchian_breakout,
    "supertrend": make_supertrend_strategy,
    "adx_ma_filter": make_adx_ma_filter,
}

STRATEGY_METADATA = {
    "ma_cross": {
        "label": "双均线交叉",
        "description": "双均线交叉：MA_fast 上穿 MA_slow 买入，下穿卖出。参数：fast(5), slow(20)",
        "category": "trend",
        "risk_level": "medium",
        "default_params": {"fast": 5, "slow": 20},
    },
    "rsi": {
        "label": "RSI 超买超卖",
        "description": "RSI 超买超卖：RSI 从超卖区回升买入，从超买区回落卖出。参数：period(14), oversold(30), overbought(70)",
        "category": "mean_reversion",
        "risk_level": "medium",
        "default_params": {"period": 14, "oversold": 30, "overbought": 70},
    },
    "macd": {
        "label": "MACD 金叉死叉",
        "description": "MACD 金叉死叉：MACD 线上穿信号线买入，下穿卖出。参数：fast(12), slow(26), signal(9)",
        "category": "trend",
        "risk_level": "medium",
        "default_params": {"fast": 12, "slow": 26, "signal": 9},
    },
    "bollinger": {
        "label": "布林带突破",
        "description": "布林带突破：价格从下轨反弹买入，上轨回落或跌破中轨卖出。参数：period(20), std_dev(2.0)",
        "category": "mean_reversion",
        "risk_level": "medium",
        "default_params": {"period": 20, "std_dev": 2.0},
    },
    "beiming": {
        "label": "北冥有鱼",
        "description": "北冥有鱼（long版）：识别犹豫区后强实体上破压力T线买入；止损/止盈/反向犹豫卖出。参数：overlap(5), oscillation_ratio(0.05)",
        "category": "breakout",
        "risk_level": "high",
        "default_params": {
            "overlap": 5,
            "oscillation_ratio": 0.05,
            "breakout_body_min": 0.8,
            "breakout_shadow_max": 0.1,
            "stop_loss_ratio": 0.01,
            "profit_loss_ratio": 9.0,
        },
    },
    "donchian_breakout": {
        "label": "Donchian 海龟突破",
        "description": "Donchian 海龟突破：突破N日高点买入，跌破M日低点卖出。参数：entry_period(20), exit_period(10)",
        "category": "breakout",
        "risk_level": "medium",
        "default_params": {"entry_period": 20, "exit_period": 10},
    },
    "supertrend": {
        "label": "SuperTrend 趋势跟随",
        "description": "SuperTrend 趋势跟随：上穿趋势线买入，下穿卖出。参数：period(10), multiplier(3.0)",
        "category": "trend",
        "risk_level": "medium",
        "default_params": {"period": 10, "multiplier": 3.0},
    },
    "adx_ma_filter": {
        "label": "ADX过滤双均线",
        "description": "ADX过滤双均线：金叉且ADX达阈值买入，死叉卖出。参数：fast(10), slow(30), adx_period(14), adx_threshold(20)",
        "category": "trend",
        "risk_level": "low",
        "default_params": {"fast": 10, "slow": 30, "adx_period": 14, "adx_threshold": 20},
    },
}

STRATEGY_DESCRIPTIONS = {
    name: meta["description"]
    for name, meta in STRATEGY_METADATA.items()
}


def list_strategy_names() -> list[str]:
    return list(STRATEGY_REGISTRY.keys())


def list_strategy_metadata() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in STRATEGY_REGISTRY.keys():
        meta = STRATEGY_METADATA.get(name, {})
        rows.append(
            {
                "name": name,
                "label": meta.get("label", name),
                "description": meta.get("description", ""),
                "category": meta.get("category", "other"),
                "risk_level": meta.get("risk_level", "medium"),
                "default_params": dict(meta.get("default_params", {})),
            }
        )
    return rows


def _coerce_factory_kwarg(val: Any, default: Any) -> Any:
    """将 JSON/表单传入的值转为与工厂函数默认值相近的类型。"""
    if default is inspect.Parameter.empty:
        return val
    if isinstance(default, bool):
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        s = str(val).strip().lower()
        return s in ("1", "true", "yes", "on")
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(val)
        except (TypeError, ValueError):
            return default
    return val


def get_strategy(name: str, params: dict | None = None):
    """
    根据名称和参数创建策略函数
    name: "ma_cross" | "rsi" | "macd" | "bollinger" | "beiming" | "donchian_breakout" | "supertrend" | "adx_ma_filter"
    params: 策略参数字典，不传则使用默认值；仅接受工厂函数签名中的参数名，未知键忽略。
    """
    factory = STRATEGY_REGISTRY.get(name)
    if factory is None:
        raise ValueError(f"未知策略: {name}，可用策略: {list(STRATEGY_REGISTRY.keys())}")
    sig = inspect.signature(factory)
    kwargs: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty:
            continue
        kwargs[pname] = param.default
    raw = params or {}
    for pname, param in sig.parameters.items():
        if pname not in raw:
            continue
        default = param.default if param.default is not inspect.Parameter.empty else None
        kwargs[pname] = _coerce_factory_kwarg(raw[pname], default)
    return factory(**kwargs)