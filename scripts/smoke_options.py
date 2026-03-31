"""
端到端 smoke（需本地 API 已启动）:
python scripts/smoke_options.py --base http://127.0.0.1:8000 --symbol AAPL.US
"""
from __future__ import annotations

import argparse
import json
import requests


def _check(resp: requests.Response, name: str) -> None:
    if resp.status_code >= 400:
        raise SystemExit(f"[FAIL] {name} -> {resp.status_code} {resp.text}")
    print(f"[OK] {name}: {resp.status_code}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--symbol", default="AAPL.US")
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    r1 = requests.get(f"{base}/options/expiries", params={"symbol": args.symbol}, timeout=10)
    _check(r1, "expiries")
    expiries = r1.json().get("expiries", [])
    expiry = expiries[0] if expiries else None

    params = {"symbol": args.symbol}
    if expiry:
        params["expiry_date"] = expiry
    r2 = requests.get(f"{base}/options/chain", params=params, timeout=10)
    _check(r2, "chain")
    chain = r2.json().get("options", [])
    if not chain:
        print("[WARN] chain empty, skip order smoke")
        return

    call_symbol = chain[0].get("call_symbol")
    put_symbol = chain[0].get("put_symbol")
    fee_payload = {
        "legs": [
            {"symbol": call_symbol, "side": "buy", "contracts": 1, "price": 1.0},
            {"symbol": put_symbol, "side": "sell", "contracts": 1, "price": 1.0},
        ]
    }
    r3 = requests.post(f"{base}/options/fee-estimate", json=fee_payload, timeout=10)
    _check(r3, "fee-estimate")

    bt_payload = {"symbol": args.symbol, "template": "straddle", "days": 180, "holding_days": 20, "contracts": 1}
    r4 = requests.post(f"{base}/options/backtest", json=bt_payload, timeout=20)
    _check(r4, "backtest")

    print(json.dumps({"chain_count": len(chain), "fee": r3.json().get("estimate"), "backtest": r4.json().get("stats")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
