from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


class TradeGuard:
    name = "base_guard"

    def check(
        self,
        symbol: str,
        action: str,
        now: datetime,
        config: dict[str, Any],
        executed_trades: list[dict[str, Any]],
        positions: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        raise NotImplementedError


class SymbolCooldownGuard(TradeGuard):
    name = "symbol_cooldown"

    def check(
        self,
        symbol: str,
        action: str,
        now: datetime,
        config: dict[str, Any],
        executed_trades: list[dict[str, Any]],
        positions: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        cooldown_min = max(0, int(config.get("same_symbol_cooldown_minutes", 30) or 0))
        if cooldown_min <= 0:
            return True, ""
        sym = str(symbol).upper().strip()
        latest_dt: Optional[datetime] = None
        for t in executed_trades:
            if str(t.get("symbol", "")).upper() != sym:
                continue
            if str(t.get("action", "")).lower() != str(action).lower():
                continue
            ts = t.get("executed_at")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(str(ts))
            except Exception:
                continue
            if latest_dt is None or dt > latest_dt:
                latest_dt = dt
        if latest_dt is None:
            return True, ""
        elapsed = (now - latest_dt).total_seconds() / 60.0
        if elapsed < cooldown_min:
            return False, f"symbol_cooldown({elapsed:.1f}/{cooldown_min}m)"
        return True, ""


class DailyTradeLimitGuard(TradeGuard):
    name = "daily_trade_limit"

    def check(
        self,
        symbol: str,
        action: str,
        now: datetime,
        config: dict[str, Any],
        executed_trades: list[dict[str, Any]],
        positions: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        sym = str(symbol).upper().strip()
        today = now.strftime("%Y-%m-%d")
        if action == "sell":
            max_per_day = max(1, int(config.get("same_symbol_max_sells_per_day", 1) or 1))
        else:
            max_per_day = max(1, int(config.get("same_symbol_max_trades_per_day", 1) or 1))
        count = 0
        for t in executed_trades:
            if str(t.get("symbol", "")).upper() != sym:
                continue
            if str(t.get("action", "")).lower() != str(action).lower():
                continue
            ts = str(t.get("executed_at", ""))
            if ts.startswith(today):
                count += 1
        if count >= max_per_day:
            return False, f"symbol_daily_limit({count}/{max_per_day})"
        return True, ""


class ExistingPositionGuard(TradeGuard):
    name = "existing_position_guard"

    def check(
        self,
        symbol: str,
        action: str,
        now: datetime,
        config: dict[str, Any],
        executed_trades: list[dict[str, Any]],
        positions: Optional[dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        if action != "buy":
            return True, ""
        if not bool(config.get("avoid_add_to_existing_position", True)):
            return True, ""
        if not positions:
            return True, ""
        sym = str(symbol).upper().strip()
        for p in positions.get("positions", []):
            if str(p.get("symbol", "")).upper() != sym:
                continue
            qty = float(p.get("quantity", 0) or 0)
            if qty > 0:
                return False, "existing_position_block"
        return True, ""

