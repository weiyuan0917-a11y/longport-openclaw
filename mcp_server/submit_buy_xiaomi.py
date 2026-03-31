"""
市价买入 01810.HK 1000 股（与 MCP submit_order 逻辑一致，含风控）
运行：在项目根目录 PYTHONPATH=D:\\longport-openclaw python mcp_server/submit_buy_xiaomi.py
"""
import sys
import os
import json
import urllib.request
from datetime import datetime

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)
root = os.path.dirname(_dir)
if root not in sys.path:
    sys.path.insert(0, root)

_API_BASE_URL = str(os.getenv("API_BASE_URL", "http://127.0.0.1:8000")).strip().rstrip("/")
_API_TIMEOUT = max(1.0, float(os.getenv("API_TIMEOUT_SECONDS", "12")))

SYMBOL = "01810.HK"
QUANTITY = 1000
ACTION = "buy"


def main():
    payload = {
        "action": ACTION,
        "symbol": SYMBOL,
        "quantity": QUANTITY,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{_API_BASE_URL}/trade/order", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
        out = {
            "订单已提交": {
                "订单ID": data.get("order_id", ""),
                "股票": SYMBOL,
                "动作": ACTION,
                "数量": QUANTITY,
                "价格": "市价",
                "时间": datetime.now().isoformat(),
                "风控": "通过",
            }
        }
        print(" [OK] 下单成功")
        msg = json.dumps(out, indent=2, ensure_ascii=False)
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode("gbk", errors="replace").decode("gbk"))
    except Exception as e:
        print(" [X] 下单失败(API):", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
