"""LongPort 历史 K 线请求的进程内优先级门控（并发槽位 + 等待队列按优先级出队）。

用于 research / 强势股刷新 / Worker 扫描等同时触发时，让高优先级请求（如实盘 Worker）更易先拿到槽位。
跨进程无效：每个 Python 进程各自一份门控状态。
"""

from __future__ import annotations

import heapq
import itertools
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

PRIORITY_HIGH = 100
PRIORITY_NORMAL = 50
PRIORITY_LOW = 10

_prio_ctx: ContextVar[int] = ContextVar("longport_history_fetch_priority", default=PRIORITY_NORMAL)

_MAX_SLOTS = max(1, int(os.getenv("LONGPORT_HISTORY_MAX_CONCURRENCY", "2")))
_LOW_YIELD_MS = max(0, int(os.getenv("LONGPORT_HISTORY_LOW_YIELD_MS", "80")))

_lock = threading.Lock()
_available = _MAX_SLOTS
_heap: list[tuple[int, int, threading.Event, list[bool]]] = []
_seq = itertools.count()


def current_priority() -> int:
    return int(_prio_ctx.get())


@contextmanager
def longport_history_priority(priority: int):
    tok = _prio_ctx.set(int(priority))
    try:
        yield
    finally:
        _prio_ctx.reset(tok)


def coalesce_priority_param(raw: str | None) -> int | None:
    """解析 query/header 中的优先级字符串，未知则返回 None（保持 ContextVar 当前值）。"""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("high", "h", "worker", "scan", "auto_trader", "autotrader"):
        return PRIORITY_HIGH
    if s in ("low", "l", "research", "backtest"):
        return PRIORITY_LOW
    if s in ("normal", "n", "default", "medium"):
        return PRIORITY_NORMAL
    try:
        v = int(s)
        return max(0, min(200, v))
    except ValueError:
        return None


@contextmanager
def using_priority_param(raw: str | None):
    p = coalesce_priority_param(raw)
    if p is None:
        yield
        return
    with longport_history_priority(p):
        yield


def acquire_history_slot(timeout: float = 25.0) -> bool:
    global _available
    pri = int(_prio_ctx.get())
    if pri <= PRIORITY_LOW and _LOW_YIELD_MS > 0:
        time.sleep(_LOW_YIELD_MS / 1000.0)
    deadline = time.monotonic() + max(0.5, float(timeout))
    with _lock:
        if _available > 0:
            _available -= 1
            return True
        ev = threading.Event()
        cancelled = [False]
        heapq.heappush(_heap, (-pri, next(_seq), ev, cancelled))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        with _lock:
            cancelled[0] = True
        return False
    if ev.wait(timeout=remaining):
        return True
    with _lock:
        cancelled[0] = True
    return False


def release_history_slot() -> None:
    global _available
    with _lock:
        while _heap:
            _neg_pri, _seq_id, ev, cancelled = heapq.heappop(_heap)
            if cancelled[0]:
                continue
            ev.set()
            return
        _available += 1
