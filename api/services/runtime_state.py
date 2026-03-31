from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from longport.openapi import QuoteContext, TradeContext


@dataclass
class RuntimeState:
    # LongPort contexts
    ctx_lock: threading.RLock = field(default_factory=threading.RLock)
    quote_ctx: Optional[QuoteContext] = None
    trade_ctx: Optional[TradeContext] = None
    longport_last_error: Optional[str] = None
    longport_last_init_at: Optional[str] = None
    longport_connect_breaker_until_ts: float = 0.0
    longport_last_reset_ts: float = 0.0

    # Research runtime states
    research_tasks_lock: threading.RLock = field(default_factory=threading.RLock)
    research_tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    research_busy_lock: threading.RLock = field(default_factory=threading.RLock)
    research_busy_active: int = 0

    # Managed subprocess handles (feishu/supervisor, etc.)
    managed_processes: dict[str, Any] = field(default_factory=dict)


_RUNTIME_STATE = RuntimeState()


def get_runtime_state() -> RuntimeState:
    return _RUNTIME_STATE

