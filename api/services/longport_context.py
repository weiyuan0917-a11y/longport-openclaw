from __future__ import annotations

from typing import Any


def collect_longport_context_snapshot(
    *,
    quote_ready: bool,
    trade_ready: bool,
    connection_limit: int,
    last_error: str | None,
    last_init_at: str | None,
) -> dict[str, Any]:
    """
    Normalize LongPort context status snapshot used by diagnostics payload.
    """
    active_connections = int(bool(quote_ready)) + int(bool(trade_ready))
    usage_pct = round(active_connections / max(1, int(connection_limit)) * 100, 2)
    return {
        "active_connections": active_connections,
        "usage_pct": usage_pct,
        "quote_ready": bool(quote_ready),
        "trade_ready": bool(trade_ready),
        "last_error": last_error,
        "last_init_at": last_init_at,
    }

