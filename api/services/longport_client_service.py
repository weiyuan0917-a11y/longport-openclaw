from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Callable

from api.services.runtime_state import RuntimeState

LONGPORT_CONNECT_BREAKER_SECONDS = max(
    5.0, float(os.getenv("LONGPORT_CONNECT_BREAKER_SECONDS", "45"))
)
LONGPORT_RESET_MIN_INTERVAL_SECONDS = max(
    0.5, float(os.getenv("LONGPORT_RESET_MIN_INTERVAL_SECONDS", "3"))
)


def is_longport_connect_error(err: Exception | str) -> bool:
    text = str(err or "").lower()
    return any(
        key in text
        for key in (
            "openapiexception",
            "client error (connect)",
            "error sending request for url",
            "/v1/socket/token",
            "connection reset",
            "name or service not known",
            "timed out",
            "connection refused",
            "breaker_open",
        )
    )


def can_try_context_init(state: RuntimeState) -> bool:
    return time.time() >= float(state.longport_connect_breaker_until_ts or 0.0)


def mark_context_connect_success(state: RuntimeState) -> None:
    state.longport_connect_breaker_until_ts = 0.0
    state.longport_last_error = None
    state.longport_last_init_at = datetime.now().isoformat()


def mark_context_connect_failure(state: RuntimeState, err: Exception | str) -> None:
    if is_longport_connect_error(err):
        state.longport_connect_breaker_until_ts = time.time() + LONGPORT_CONNECT_BREAKER_SECONDS
    state.longport_last_error = str(err)


def throttled_reset_contexts(reset_fn: Callable[[], None], state: RuntimeState) -> bool:
    now = time.time()
    last = float(state.longport_last_reset_ts or 0.0)
    if (now - last) < LONGPORT_RESET_MIN_INTERVAL_SECONDS:
        return False
    state.longport_last_reset_ts = now
    try:
        reset_fn()
        return True
    except Exception:
        return False

