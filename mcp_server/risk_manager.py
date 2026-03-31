"""
risk_manager.py - 风控系统核心
支持：
  - 单笔订单金额上限
  - 单日最大亏损限额
  - 持仓止损（跌X%自动平仓）
  - 单只股票最大仓位比例
  - 参数存储在 JSON 配置文件，支持 MCP 动态修改
"""
import json
import os
import sys
import re
from datetime import datetime, date
from decimal import Decimal
from dataclasses import dataclass, asdict
from typing import Optional

# 配置文件路径
RISK_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "risk_config.json"
)

# 交易日志路径（用于计算当日盈亏）
TRADE_LOG_PATH = os.path.join(
    os.path.dirname(__file__), "trade_log.json"
)

OPTION_CONTRACT_MULTIPLIER = 100.0


def _is_option_symbol(symbol: str) -> bool:
    s = str(symbol or "").upper()
    if ".US" in s and (" C " in s or " P " in s):
        return True
    return bool(re.search(r"\d{6,8}[CP]\d+", s))


def _trade_multiplier(symbol: str) -> float:
    # 美股期权默认 1 张 = 100 股
    return OPTION_CONTRACT_MULTIPLIER if _is_option_symbol(symbol) else 1.0


def trade_value(symbol: str, quantity: float, price: float) -> float:
    return float(quantity) * float(price) * _trade_multiplier(symbol)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class RiskConfig:
    """风控参数"""
    # 单笔订单金额上限（USD/HKD，取决于账户货币）
    max_order_amount: float = 100_000.0
    # 单日最大亏损限额（占总资产比例，0.05 = 5%）
    max_daily_loss_pct: float = 0.05
    # 持仓止损线（跌幅超过此值自动平仓，0.05 = 5%）
    stop_loss_pct: float = 0.05
    # 单只股票最大仓位比例（占总资产，0.20 = 20%）
    max_position_pct: float = 0.20
    # 是否启用风控（False = 只警告不拦截）
    enabled: bool = True
    # 最后更新时间
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RiskConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    passed: bool
    rule: str          # 触发的规则名称
    reason: str        # 拒绝原因（passed=True 时为空）
    detail: dict       # 详细数据


@dataclass
class StopLossCheck:
    """止损检查结果"""
    symbol: str
    should_stop: bool
    current_price: float
    cost_price: float
    loss_pct: float
    threshold_pct: float
    quantity: float


# ============================================================
# 配置读写
# ============================================================

def load_config() -> RiskConfig:
    """加载风控配置"""
    if not os.path.exists(RISK_CONFIG_PATH):
        cfg = RiskConfig(updated_at=datetime.now().isoformat())
        save_config(cfg)
        return cfg
    try:
        with open(RISK_CONFIG_PATH, "r", encoding="utf-8") as f:
            return RiskConfig.from_dict(json.load(f))
    except Exception:
        return RiskConfig(updated_at=datetime.now().isoformat())


def save_config(cfg: RiskConfig) -> None:
    """保存风控配置"""
    cfg.updated_at = datetime.now().isoformat()
    os.makedirs(os.path.dirname(RISK_CONFIG_PATH), exist_ok=True)
    with open(RISK_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, indent=2, ensure_ascii=False)


# ============================================================
# 交易日志（用于计算当日盈亏）
# ============================================================

def load_trade_log() -> list:
    if not os.path.exists(TRADE_LOG_PATH):
        return []
    try:
        with open(TRADE_LOG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def append_trade_log(entry: dict) -> None:
    logs = load_trade_log()
    logs.append(entry)
    os.makedirs(os.path.dirname(TRADE_LOG_PATH), exist_ok=True)
    with open(TRADE_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)


def get_today_realized_pnl() -> float:
    """计算今日已实现盈亏"""
    today = date.today().isoformat()
    logs = load_trade_log()
    total = 0.0
    for log in logs:
        if log.get("date") == today and log.get("type") == "realized_pnl":
            total += float(log.get("amount", 0))
    return total


# ============================================================
# 风控规则实现
# ============================================================

class RiskManager:
    def __init__(self):
        self.cfg = load_config()

    def reload(self):
        """重新加载配置"""
        self.cfg = load_config()

    # ─── 规则 1: 单笔订单金额上限 ─────────────────────────────

    def check_order_amount(
        self,
        quantity: int,
        price: float,
        symbol: str,
    ) -> RiskCheckResult:
        """检查单笔订单金额是否超限"""
        multiplier = _trade_multiplier(symbol)
        order_amount = trade_value(symbol, quantity, price)
        limit = self.cfg.max_order_amount

        if order_amount > limit:
            return RiskCheckResult(
                passed=False,
                rule="单笔金额上限",
                reason=(
                    f"订单金额 {order_amount:,.2f} 超过限额 {limit:,.2f}。"
                    f"建议将数量降至 {int(limit / (price * multiplier))} {'张' if multiplier > 1 else '股'}以内。"
                ),
                detail={
                    "order_amount": order_amount,
                    "limit": limit,
                    "quantity": quantity,
                    "price": price,
                    "symbol": symbol,
                    "multiplier": multiplier,
                },
            )
        return RiskCheckResult(
            passed=True, rule="单笔金额上限", reason="",
            detail={"order_amount": order_amount, "limit": limit, "multiplier": multiplier},
        )

    # ─── 规则 2: 单日最大亏损 ─────────────────────────────────

    def check_daily_loss(self, total_assets: float) -> RiskCheckResult:
        """检查今日亏损是否超限"""
        today_pnl = get_today_realized_pnl()
        limit_amount = total_assets * self.cfg.max_daily_loss_pct

        if today_pnl < 0 and abs(today_pnl) >= limit_amount:
            return RiskCheckResult(
                passed=False,
                rule="单日亏损限额",
                reason=(
                    f"今日已亏损 {abs(today_pnl):,.2f}，"
                    f"已达单日上限（总资产 {self.cfg.max_daily_loss_pct*100:.0f}% = {limit_amount:,.2f}）。"
                    "今日不再允许开新仓。"
                ),
                detail={
                    "today_pnl": today_pnl,
                    "limit_amount": limit_amount,
                    "limit_pct": self.cfg.max_daily_loss_pct,
                    "total_assets": total_assets,
                },
            )
        return RiskCheckResult(
            passed=True, rule="单日亏损限额", reason="",
            detail={
                "today_pnl": today_pnl,
                "limit_amount": limit_amount,
                "remaining": limit_amount - abs(min(today_pnl, 0)),
            },
        )

    # ─── 规则 3: 单只股票最大仓位比例 ────────────────────────

    def check_position_size(
        self,
        symbol: str,
        quantity: int,
        price: float,
        total_assets: float,
        existing_position_value: float = 0.0,
    ) -> RiskCheckResult:
        """检查买入后该股票仓位是否超比例"""
        multiplier = _trade_multiplier(symbol)
        new_value = existing_position_value + trade_value(symbol, quantity, price)
        new_pct = new_value / total_assets if total_assets else 0
        limit_pct = self.cfg.max_position_pct
        limit_value = total_assets * limit_pct

        if new_pct > limit_pct:
            max_qty = max(0, int((limit_value - existing_position_value) / (price * multiplier)))
            return RiskCheckResult(
                passed=False,
                rule="单只股票仓位上限",
                reason=(
                    f"买入后 {symbol} 仓位将达 {new_pct*100:.1f}%，"
                    f"超过上限 {limit_pct*100:.0f}%。"
                    f"最多还可买入 {max_qty} {'张' if multiplier > 1 else '股'}。"
                ),
                detail={
                    "symbol": symbol,
                    "new_position_value": new_value,
                    "new_pct": new_pct,
                    "limit_pct": limit_pct,
                    "max_additional_qty": max_qty,
                    "total_assets": total_assets,
                    "multiplier": multiplier,
                },
            )
        return RiskCheckResult(
            passed=True, rule="单只股票仓位上限", reason="",
            detail={
                "new_pct": new_pct,
                "limit_pct": limit_pct,
                "remaining_quota": limit_value - new_value,
                "multiplier": multiplier,
            },
        )

    # ─── 规则 4: 持仓止损检查 ────────────────────────────────

    def check_stop_loss(
        self,
        symbol: str,
        cost_price: float,
        current_price: float,
        quantity: float,
    ) -> StopLossCheck:
        """检查持仓是否触发止损"""
        if cost_price <= 0:
            loss_pct = 0.0
        else:
            loss_pct = (current_price - cost_price) / cost_price  # 负数 = 亏损

        should_stop = loss_pct <= -self.cfg.stop_loss_pct

        return StopLossCheck(
            symbol=symbol,
            should_stop=should_stop,
            current_price=current_price,
            cost_price=cost_price,
            loss_pct=loss_pct,
            threshold_pct=-self.cfg.stop_loss_pct,
            quantity=quantity,
        )

    # ─── 综合检查（下单前调用）───────────────────────────────

    def full_check_before_order(
        self,
        symbol: str,
        action: str,            # "buy" | "sell"
        quantity: int,
        price: float,
        total_assets: float,
        available_cash: float,
        existing_position_value: float = 0.0,
    ) -> dict:
        """
        下单前完整风控检查
        返回：{ passed: bool, blocks: [...], warnings: [...] }
        """
        self.reload()

        if not self.cfg.enabled:
            return {
                "passed": True,
                "blocks": [],
                "warnings": ["风控已关闭（仅监控模式）"],
                "summary": "风控已关闭，订单放行",
            }

        blocks = []    # 拦截项
        warnings = []  # 警告项（不拦截）

        # 仅买单做完整风控
        if action == "buy":
            # 规则 1: 金额上限
            r1 = self.check_order_amount(quantity, price, symbol)
            if not r1.passed:
                blocks.append({"rule": r1.rule, "reason": r1.reason, "detail": r1.detail})

            # 规则 2: 单日亏损
            r2 = self.check_daily_loss(total_assets)
            if not r2.passed:
                blocks.append({"rule": r2.rule, "reason": r2.reason, "detail": r2.detail})

            # 规则 3: 仓位比例
            r3 = self.check_position_size(
                symbol, quantity, price, total_assets, existing_position_value
            )
            if not r3.passed:
                blocks.append({"rule": r3.rule, "reason": r3.reason, "detail": r3.detail})

            # 资金检查（警告级别）
            order_cost = trade_value(symbol, quantity, price)
            if order_cost > available_cash:
                warnings.append(f"可用现金 {available_cash:,.2f} 不足以支付订单 {order_cost:,.2f}")

        passed = len(blocks) == 0

        return {
            "passed": passed,
            "blocks": blocks,
            "warnings": warnings,
            "summary": (
                "✅ 风控通过，可以下单" if passed
                else f"❌ 风控拦截：{blocks[0]['rule']} — {blocks[0]['reason']}"
            ),
            "checked_at": datetime.now().isoformat(),
        }


# 单例
_manager = RiskManager()


def get_manager() -> RiskManager:
    return _manager