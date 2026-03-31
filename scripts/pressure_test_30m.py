import json
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

import requests

BASE_URL = os.getenv("PRESSURE_BASE_URL", "http://127.0.0.1:8000")
DURATION_SECONDS = int(os.getenv("PRESSURE_DURATION_SECONDS", "1800"))
TIMEOUT_SECONDS = float(os.getenv("PRESSURE_TIMEOUT_SECONDS", "8"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
DETAIL_LOG = os.path.join(LOG_DIR, f"pressure-test-{STAMP}.jsonl")
SUMMARY_LOG = os.path.join(LOG_DIR, f"pressure-test-{STAMP}.summary.json")


def _write_jsonl(path: str, row: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str))
        f.write("\n")


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = int(0.95 * (len(values) - 1))
    return float(values[idx])


def main() -> None:
    schedule = [
        {"name": "health", "method": "GET", "path": "/health", "interval": 5},
        {"name": "strong_stocks_us", "method": "GET", "path": "/auto-trader/strong-stocks?market=us&limit=8&kline=1h", "interval": 10},
        {"name": "strong_stocks_hk", "method": "GET", "path": "/auto-trader/strong-stocks?market=hk&limit=8&kline=1h", "interval": 10},
        {"name": "research_status", "method": "GET", "path": "/auto-trader/research/status", "interval": 15},
        {"name": "metrics_recent", "method": "GET", "path": "/auto-trader/metrics/recent?limit=20", "interval": 20},
    ]
    last_run: dict[str, float] = {s["name"]: 0.0 for s in schedule}
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total": 0,
            "ok": 0,
            "errors": 0,
            "status_codes": defaultdict(int),
            "latencies_ms": [],
            "exceptions": defaultdict(int),
        }
    )

    started_at = time.time()
    end_at = started_at + DURATION_SECONDS
    print(f"pressure test started: duration={DURATION_SECONDS}s base={BASE_URL}")

    while time.time() < end_at:
        now = time.time()
        due = [s for s in schedule if (now - last_run[s["name"]]) >= s["interval"]]
        if not due:
            time.sleep(0.2)
            continue
        for s in due:
            name = s["name"]
            url = BASE_URL + s["path"]
            t0 = time.perf_counter()
            row: dict[str, Any] = {
                "ts": datetime.now().isoformat(),
                "endpoint": name,
                "url": s["path"],
                "ok": False,
            }
            stats[name]["total"] += 1
            try:
                resp = requests.request(s["method"], url, timeout=TIMEOUT_SECONDS)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                row["elapsed_ms"] = round(elapsed_ms, 3)
                row["status_code"] = int(resp.status_code)
                row["ok"] = 200 <= resp.status_code < 300
                if row["ok"]:
                    stats[name]["ok"] += 1
                else:
                    stats[name]["errors"] += 1
                stats[name]["status_codes"][str(resp.status_code)] += 1
                stats[name]["latencies_ms"].append(float(elapsed_ms))
            except Exception as e:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                row["elapsed_ms"] = round(elapsed_ms, 3)
                row["error"] = str(e)
                stats[name]["errors"] += 1
                stats[name]["exceptions"][type(e).__name__] += 1
            _write_jsonl(DETAIL_LOG, row)
            last_run[name] = now

    finished_at = time.time()
    summary: dict[str, Any] = {
        "started_at": datetime.fromtimestamp(started_at).isoformat(),
        "finished_at": datetime.fromtimestamp(finished_at).isoformat(),
        "duration_seconds": round(finished_at - started_at, 3),
        "base_url": BASE_URL,
        "detail_log": DETAIL_LOG,
        "endpoints": {},
    }
    for name, st in stats.items():
        lat = st["latencies_ms"]
        total = int(st["total"])
        ok = int(st["ok"])
        errors = int(st["errors"])
        summary["endpoints"][name] = {
            "total": total,
            "ok": ok,
            "errors": errors,
            "success_rate_pct": round((ok / total * 100.0), 3) if total > 0 else 0.0,
            "latency_ms_avg": round(float(statistics.mean(lat)), 3) if lat else 0.0,
            "latency_ms_p95": round(_p95(lat), 3) if lat else 0.0,
            "latency_ms_max": round(float(max(lat)), 3) if lat else 0.0,
            "status_codes": dict(st["status_codes"]),
            "exceptions": dict(st["exceptions"]),
        }
    with open(SUMMARY_LOG, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(json.dumps({"ok": True, "summary_log": SUMMARY_LOG, "detail_log": DETAIL_LOG}, ensure_ascii=False))


if __name__ == "__main__":
    main()
