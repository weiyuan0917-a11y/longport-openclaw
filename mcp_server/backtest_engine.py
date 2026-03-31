"""
backtest_engine.py - 回测引擎核心
纯 Python 实现，不依赖 Backtrader
数据来源：LongPort history_candlesticks_by_date（调用方应使用 trade_sessions=All 含盘前/盘后/夜盘）
支持指标：总收益率、年化收益、最大回撤、夏普比率、胜率/盈亏比、交易次数
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Literal, Optional
import math
from fee_model import estimate_stock_order_fee


# ============================================================
# 数据结构
# ============================================================

@dataclass
class Bar:
    """单根K线（date 为 bar 开盘/归属时刻，分钟/小时K 可区分同日多根）"""
    date:   datetime
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float


def coerce_bar_datetime(val: Any) -> datetime:
    """将 API/JSON/LongPort 时间统一为 naive datetime，便于同日多根 K 去重与排序。"""
    if isinstance(val, datetime):
        dt = val
    elif isinstance(val, date):
        return datetime.combine(val, datetime.min.time())
    else:
        s = str(val).strip()
        if not s:
            raise ValueError("empty bar time")
        if "T" in s or (len(s) > 10 and s[10] in " T"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            return datetime.combine(date.fromisoformat(s[:10]), datetime.min.time())
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@dataclass
class Trade:
    """一笔完整交易（开仓→平仓）"""
    symbol:      str
    entry_date:  date
    exit_date:   date
    entry_price: float
    exit_price:  float
    quantity:    int
    direction:   str    # "long"
    pnl:         float  # 税后盈亏（扣手续费）
    pnl_pct:     float  # 盈亏百分比
    hold_days:   int


@dataclass
class BacktestResult:
    """回测结果"""
    symbol:          str
    strategy_name:   str
    start_date:      str
    end_date:        str
    initial_capital: float

    # ── 收益指标 ──
    final_capital:    float = 0.0
    total_return_pct: float = 0.0   # 总收益率 %
    annual_return_pct: float = 0.0  # 年化收益率 %

    # ── 风险指标 ──
    max_drawdown_pct: float = 0.0   # 最大回撤 %
    sharpe_ratio:     float = 0.0   # 夏普比率（无风险利率 4%）

    # ── 交易统计 ──
    total_trades:  int   = 0
    win_trades:    int   = 0
    loss_trades:   int   = 0
    win_rate_pct:  float = 0.0   # 胜率 %
    profit_factor: float = 0.0   # 盈亏比（总盈利/总亏损）
    avg_win_pct:   float = 0.0   # 平均盈利 %
    avg_loss_pct:  float = 0.0   # 平均亏损 %
    total_commission: float = 0.0  # 总佣金估算
    total_stamp_duty: float = 0.0  # 总印花税估算（卖出侧）
    total_cost_pct_initial: float = 0.0  # 总交易成本占初始资金比例 %
    fee_breakdown: dict[str, float] = field(default_factory=dict)  # 费用拆分

    # ── 明细 ──
    trades:        list[Trade] = field(default_factory=list)
    equity_curve:  list[dict]  = field(default_factory=list)  # 资金曲线

    def to_summary(self) -> dict:
        """返回给 Claude 的 JSON 摘要"""
        return {
            "股票代码":   self.symbol,
            "策略名称":   self.strategy_name,
            "回测区间":   f"{self.start_date} ~ {self.end_date}",
            "初始资金":   f"{self.initial_capital:,.0f}",
            "最终资金":   f"{self.final_capital:,.0f}",
            "收益指标": {
                "总收益率":   f"{self.total_return_pct:+.2f}%",
                "年化收益率": f"{self.annual_return_pct:+.2f}%",
                "最大回撤":   f"-{self.max_drawdown_pct:.2f}%",
                "夏普比率":   f"{self.sharpe_ratio:.2f}",
            },
            "交易统计": {
                "总交易次数": self.total_trades,
                "盈利次数":   self.win_trades,
                "亏损次数":   self.loss_trades,
                "胜率":       f"{self.win_rate_pct:.1f}%",
                "盈亏比":     f"{self.profit_factor:.2f}",
                "平均盈利":   f"{self.avg_win_pct:.2f}%",
                "平均亏损":   f"{self.avg_loss_pct:.2f}%",
            },
            "成本估算": {
                "总佣金": round(self.total_commission, 2),
                "总印花税": round(self.total_stamp_duty, 2),
                "总成本占初始资金比例": f"{self.total_cost_pct_initial:.2f}%",
                "费用拆分": self.fee_breakdown,
            },
            "最近5笔交易": [
                {
                    "开仓日期": str(t.entry_date),
                    "平仓日期": str(t.exit_date),
                    "开仓价":   t.entry_price,
                    "平仓价":   t.exit_price,
                    "盈亏":     f"{t.pnl_pct:+.2f}%",
                    "持有天数": t.hold_days,
                }
                for t in self.trades[-5:]
            ],
            "综合评级": self._rating(),
        }

    def _rating(self) -> str:
        score = 0
        if self.total_return_pct > 20: score += 2
        elif self.total_return_pct > 0: score += 1
        if self.max_drawdown_pct < 10: score += 2
        elif self.max_drawdown_pct < 20: score += 1
        if self.sharpe_ratio > 1.5: score += 2
        elif self.sharpe_ratio > 0.5: score += 1
        if self.win_rate_pct > 55: score += 1
        if self.profit_factor > 1.5: score += 1
        return {
            range(8, 10): "⭐⭐⭐⭐⭐ 优秀",
            range(6, 8):  "⭐⭐⭐⭐ 良好",
            range(4, 6):  "⭐⭐⭐ 一般",
            range(2, 4):  "⭐⭐ 较差",
        }.get(next((r for r in [range(8,10),range(6,8),range(4,6),range(2,4)] if score in r), None), "⭐ 差")


# ============================================================
# 回测引擎
# ============================================================

class BacktestEngine:
    """
    事件驱动回测引擎
    strategy_fn: (bars_so_far: list[Bar], position: int) -> str
                 返回 "buy" | "sell" | "hold"
    """

    # 默认按市场分档（单位：bps，1bp=0.01%）
    MARKET_COMMISSION_BPS = {
        "US": 3.0,
        "HK": 8.0,
        "CN": 2.5,
        "OTHER": 5.0,
    }
    # 印花税（仅卖出侧），单位 bps
    MARKET_SELL_STAMP_DUTY_BPS = {
        "US": 0.0,
        "HK": 10.0,  # 0.10%
        "CN": 5.0,   # 0.05%
        "OTHER": 0.0,
    }
    RISK_FREE_RATE  = 0.04    # 无风险利率 4%（年）

    def __init__(
        self,
        bars:            list[Bar],
        symbol:          str,
        strategy_name:   str,
        strategy_fn:     Callable,
        initial_capital: float = 100_000.0,
        position_size:   float = 0.95,       # 每次用 95% 资金建仓
        execution_mode:  Literal["next_open", "bar_close"] = "next_open",
        slippage_bps:    float = 3.0,
        market:          str | None = None,
        commission_bps:  float | None = None,
        stamp_duty_bps:  float | None = None,
        signal_filter:   Optional[Callable[[str, list[Bar], int], bool]] = None,
    ):
        self.bars            = bars
        self.symbol          = symbol
        self.strategy_name   = strategy_name
        self.strategy_fn     = strategy_fn
        self.initial_capital = initial_capital
        self.position_size   = position_size
        self.execution_mode  = execution_mode
        self.slippage_bps    = max(0.0, float(slippage_bps))
        self.market          = (market or self._infer_market(symbol)).upper()
        self.signal_filter   = signal_filter
        bps = (
            float(commission_bps)
            if commission_bps is not None
            else float(self.MARKET_COMMISSION_BPS.get(self.market, self.MARKET_COMMISSION_BPS["OTHER"]))
        )
        duty_bps = (
            float(stamp_duty_bps)
            if stamp_duty_bps is not None
            else float(self.MARKET_SELL_STAMP_DUTY_BPS.get(self.market, self.MARKET_SELL_STAMP_DUTY_BPS["OTHER"]))
        )
        self.commission_rate = max(0.0, bps) / 10_000
        self.sell_stamp_duty_rate = max(0.0, duty_bps) / 10_000
        self._use_legacy_fee = (commission_bps is not None) or (stamp_duty_bps is not None) or (self.market not in {"HK", "US"})

    def _calc_order_fee(
        self, side: Literal["buy", "sell"], quantity: int, exec_price: float
    ) -> tuple[float, float, float, dict[str, float]]:
        gross = max(0.0, float(quantity) * float(exec_price))
        if self._use_legacy_fee:
            commission = gross * self.commission_rate
            stamp_duty = gross * self.sell_stamp_duty_rate if side == "sell" else 0.0
            total_fee = commission + stamp_duty
            breakdown = {
                "commission": float(commission),
                "stamp_duty": float(stamp_duty),
            }
            return total_fee, commission, stamp_duty, breakdown
        est = estimate_stock_order_fee(
            market=self.market, side=side, quantity=int(quantity), price=float(exec_price)
        )
        total_fee = float(est.get("total_fee", 0.0))
        stamp_duty = float(est.get("stamp_duty", 0.0))
        commission = float(est.get("commission_like", max(0.0, total_fee - stamp_duty)))
        breakdown = {k: float(v) for k, v in (est.get("components") or {}).items()}
        if stamp_duty > 0:
            breakdown["stamp_duty"] = float(stamp_duty)
        return total_fee, commission, stamp_duty, breakdown

    def _buy_price(self, bar: Bar) -> float:
        base = bar.open if self.execution_mode == "next_open" else bar.close
        return float(base) * (1 + self.slippage_bps / 10_000)

    def _sell_price(self, bar: Bar) -> float:
        base = bar.open if self.execution_mode == "next_open" else bar.close
        return float(base) * (1 - self.slippage_bps / 10_000)

    @staticmethod
    def _infer_market(symbol: str) -> str:
        s = str(symbol or "").upper()
        if s.endswith(".US"):
            return "US"
        if s.endswith(".HK"):
            return "HK"
        if s.endswith(".SH") or s.endswith(".SZ") or s.endswith(".CN"):
            return "CN"
        return "OTHER"

    def run(self) -> BacktestResult:
        bars   = self.bars
        cash   = self.initial_capital
        pos    = 0       # 持仓数量
        entry_price = 0.0
        entry_cal_date: date | None = None  # 日历日，用于 Trade 与持有天数
        entry_total_cost = 0.0
        trades: list[Trade] = []
        equity: list[dict]  = []

        peak_equity = self.initial_capital
        max_dd      = 0.0
        daily_returns: list[float] = []
        prev_equity = self.initial_capital
        pending_signal: str | None = None
        seen_bars: list[Bar] = []
        total_commission = 0.0
        total_stamp_duty = 0.0
        fee_breakdown_total: dict[str, float] = {}

        def _accumulate_fee_breakdown(parts: dict[str, float]) -> None:
            for k, v in parts.items():
                fee_breakdown_total[k] = fee_breakdown_total.get(k, 0.0) + float(v or 0.0)

        for i, bar in enumerate(bars):
            seen_bars.append(bar)

            # 先执行上一根K线产生的信号（下一根执行），减少同K线前视偏差
            if self.execution_mode == "next_open" and pending_signal:
                if pending_signal == "buy" and pos == 0 and cash > 0:
                    buy_cash = cash * self.position_size
                    exec_price = self._buy_price(bar)
                    qty = int(buy_cash / exec_price)
                    if qty > 0:
                        gross = qty * exec_price
                        buy_total_fee, buy_commission, buy_stamp_duty, buy_breakdown = self._calc_order_fee("buy", qty, exec_price)
                        cost = gross + buy_total_fee
                        total_commission += buy_commission
                        total_stamp_duty += buy_stamp_duty
                        _accumulate_fee_breakdown(buy_breakdown)
                        cash -= cost
                        pos = qty
                        entry_price = exec_price
                        entry_cal_date = bar.date.date()
                        entry_total_cost = cost
                elif pending_signal == "sell" and pos > 0:
                    exec_price = self._sell_price(bar)
                    gross = pos * exec_price
                    sell_total_fee, sell_commission, sell_stamp_duty, sell_breakdown = self._calc_order_fee("sell", pos, exec_price)
                    proceeds = gross - sell_total_fee
                    total_commission += sell_commission
                    total_stamp_duty += sell_stamp_duty
                    _accumulate_fee_breakdown(sell_breakdown)
                    pnl = proceeds - entry_total_cost
                    pnl_pct = (exec_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
                    exd = bar.date.date()
                    trades.append(Trade(
                        symbol=self.symbol,
                        entry_date=entry_cal_date or exd,
                        exit_date=exd,
                        entry_price=round(entry_price, 4),
                        exit_price=round(exec_price, 4),
                        quantity=pos,
                        direction="long",
                        pnl=round(pnl, 2),
                        pnl_pct=round(pnl_pct, 2),
                        hold_days=(exd - (entry_cal_date or exd)).days,
                    ))
                    cash += proceeds
                    pos = 0
                    entry_price = 0.0
                    entry_cal_date = None
                    entry_total_cost = 0.0
                pending_signal = None

            # 当前总资产
            cur_equity = cash + pos * bar.close
            equity.append({"date": str(bar.date), "equity": round(cur_equity, 2)})

            # 最大回撤
            if cur_equity > peak_equity:
                peak_equity = cur_equity
            dd = (peak_equity - cur_equity) / peak_equity * 100
            if dd > max_dd:
                max_dd = dd

            # 日收益率
            if prev_equity > 0:
                daily_returns.append((cur_equity - prev_equity) / prev_equity)
            prev_equity = cur_equity

            # 策略信号
            signal = self.strategy_fn(seen_bars, pos)
            if signal in {"buy", "sell"} and self.signal_filter is not None:
                try:
                    if not bool(self.signal_filter(signal, seen_bars, pos)):
                        signal = "hold"
                except Exception:
                    signal = "hold"

            if self.execution_mode == "next_open":
                if signal in {"buy", "sell"}:
                    pending_signal = signal
            else:
                if signal == "buy" and pos == 0 and cash > 0:
                    buy_cash = cash * self.position_size
                    exec_price = self._buy_price(bar)
                    qty = int(buy_cash / exec_price)
                    if qty > 0:
                        gross = qty * exec_price
                        buy_total_fee, buy_commission, buy_stamp_duty, buy_breakdown = self._calc_order_fee("buy", qty, exec_price)
                        cost = gross + buy_total_fee
                        total_commission += buy_commission
                        total_stamp_duty += buy_stamp_duty
                        _accumulate_fee_breakdown(buy_breakdown)
                        cash -= cost
                        pos = qty
                        entry_price = exec_price
                        entry_cal_date = bar.date.date()
                        entry_total_cost = cost

                elif signal == "sell" and pos > 0:
                    exec_price = self._sell_price(bar)
                    gross = pos * exec_price
                    sell_total_fee, sell_commission, sell_stamp_duty, sell_breakdown = self._calc_order_fee("sell", pos, exec_price)
                    proceeds = gross - sell_total_fee
                    total_commission += sell_commission
                    total_stamp_duty += sell_stamp_duty
                    _accumulate_fee_breakdown(sell_breakdown)
                    pnl = proceeds - entry_total_cost
                    pnl_pct = (exec_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
                    exd = bar.date.date()
                    trades.append(Trade(
                        symbol=self.symbol,
                        entry_date=entry_cal_date or exd,
                        exit_date=exd,
                        entry_price=round(entry_price, 4),
                        exit_price=round(exec_price, 4),
                        quantity=pos,
                        direction="long",
                        pnl=round(pnl, 2),
                        pnl_pct=round(pnl_pct, 2),
                        hold_days=(exd - (entry_cal_date or exd)).days,
                    ))
                    cash += proceeds
                    pos = 0
                    entry_price = 0.0
                    entry_cal_date = None
                    entry_total_cost = 0.0

        # 未平仓的强制平仓
        if pos > 0 and bars:
            last = bars[-1]
            exec_price = self._sell_price(last)
            gross = pos * exec_price
            sell_total_fee, sell_commission, sell_stamp_duty, sell_breakdown = self._calc_order_fee("sell", pos, exec_price)
            proceeds = gross - sell_total_fee
            total_commission += sell_commission
            total_stamp_duty += sell_stamp_duty
            _accumulate_fee_breakdown(sell_breakdown)
            pnl      = proceeds - entry_total_cost
            pnl_pct  = (exec_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
            lxd = last.date.date()
            trades.append(Trade(
                symbol=self.symbol,
                entry_date=entry_cal_date or lxd,
                exit_date=lxd,
                entry_price=round(entry_price,4), exit_price=round(exec_price,4),
                quantity=pos, direction="long",
                pnl=round(pnl,2), pnl_pct=round(pnl_pct,2),
                hold_days=(lxd - (entry_cal_date or lxd)).days,
            ))
            cash += proceeds

        # ── 计算汇总指标 ──
        final_capital    = cash
        total_return_pct = (final_capital - self.initial_capital) / self.initial_capital * 100

        # 年化收益率
        n_days = (bars[-1].date - bars[0].date).days if bars else 1
        annual_return_pct = (
            ((final_capital / self.initial_capital) ** (365 / max(n_days, 1)) - 1) * 100
        ) if n_days > 0 else 0

        # 夏普比率
        if daily_returns and len(daily_returns) > 1:
            avg_r  = sum(daily_returns) / len(daily_returns)
            std_r  = math.sqrt(sum((r - avg_r)**2 for r in daily_returns) / (len(daily_returns)-1))
            rf_day = self.RISK_FREE_RATE / 252
            sharpe = ((avg_r - rf_day) / std_r * math.sqrt(252)) if std_r > 0 else 0
        else:
            sharpe = 0

        # 胜率 / 盈亏比
        wins   = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        win_rate    = len(wins) / len(trades) * 100 if trades else 0
        total_win   = sum(t.pnl for t in wins)
        total_loss  = abs(sum(t.pnl for t in losses))
        profit_factor = total_win / total_loss if total_loss > 0 else float("inf")
        avg_win_pct  = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss_pct = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
        total_cost = total_commission + total_stamp_duty
        total_cost_pct_initial = (
            (total_cost / self.initial_capital * 100)
            if self.initial_capital > 0 else 0.0
        )

        return BacktestResult(
            symbol           = self.symbol,
            strategy_name    = self.strategy_name,
            start_date       = str(bars[0].date)  if bars else "",
            end_date         = str(bars[-1].date) if bars else "",
            initial_capital  = self.initial_capital,
            final_capital    = round(final_capital, 2),
            total_return_pct = round(total_return_pct, 2),
            annual_return_pct= round(annual_return_pct, 2),
            max_drawdown_pct = round(max_dd, 2),
            sharpe_ratio     = round(sharpe, 2),
            total_trades     = len(trades),
            win_trades       = len(wins),
            loss_trades      = len(losses),
            win_rate_pct     = round(win_rate, 1),
            profit_factor    = round(profit_factor, 2),
            avg_win_pct      = round(avg_win_pct, 2),
            avg_loss_pct     = round(avg_loss_pct, 2),
            total_commission = round(total_commission, 2),
            total_stamp_duty = round(total_stamp_duty, 2),
            total_cost_pct_initial = round(total_cost_pct_initial, 4),
            fee_breakdown = {k: round(v, 2) for k, v in fee_breakdown_total.items() if abs(v) > 1e-12},
            trades           = trades,
            equity_curve     = equity,
        )
