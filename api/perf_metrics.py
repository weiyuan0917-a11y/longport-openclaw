import json
import os
import threading
from datetime import datetime
from typing import Any, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRIC_FILE = os.path.join(ROOT, "logs", "auto_trader_metrics.jsonl")
_LOCK = threading.RLock()


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def emit_metric(
    event: str,
    ok: bool = True,
    elapsed_ms: Optional[float] = None,
    tags: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    try:
        payload: dict[str, Any] = {
            "ts": datetime.now().isoformat(),
            "event": str(event),
            "ok": bool(ok),
        }
        if elapsed_ms is not None:
            payload["elapsed_ms"] = round(float(elapsed_ms), 3)
        if tags:
            payload["tags"] = dict(tags)
        if extra:
            payload["extra"] = dict(extra)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with _LOCK:
            _ensure_parent(METRIC_FILE)
            with open(METRIC_FILE, "a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
    except Exception:
        # 指标写入不能影响主链路。
        pass


def read_recent_metrics(limit: int = 200, event: Optional[str] = None) -> list[dict[str, Any]]:
    n = max(1, min(2000, int(limit)))
    with _LOCK:
        if not os.path.exists(METRIC_FILE):
            return []
        try:
            with open(METRIC_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            return []
    out: list[dict[str, Any]] = []
    expected = str(event).strip() if event else ""
    for raw in reversed(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if expected and str(row.get("event", "")) != expected:
            continue
        out.append(row)
        if len(out) >= n:
            break
    out.reverse()
    return out
