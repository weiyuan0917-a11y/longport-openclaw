"""
每小时将「市场分析」+「RXRX.US / HOOD.US 股票分析」发送到飞书。
需配置：LongPort（股票行情）、notification_config.json（飞书 Webhook）。
运行方式（任选其一）：
  1) 前台循环：python mcp_server/hourly_market_stocks_to_feishu.py
  2) 后台：nohup python mcp_server/hourly_market_stocks_to_feishu.py &
  Windows 后台：pythonw hourly_market_stocks_to_feishu.py 或 任务计划程序
"""
import sys
import os
import time
import json
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)
root = os.path.dirname(_dir)
if root not in sys.path:
    sys.path.insert(0, root)

_API_BASE_URL = str(os.getenv("API_BASE_URL", "http://127.0.0.1:8000")).strip().rstrip("/")
_API_TIMEOUT = max(1.0, float(os.getenv("API_TIMEOUT_SECONDS", "10")))

STOCK_SYMBOLS = [
    ("RXRX.US", "Recursion Pharmaceuticals"),
    ("HOOD.US", "Robinhood"),
]
INTERVAL_SECONDS = 3600


def _api_get_json(path: str, params: dict | None = None) -> dict | None:
    q = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{_API_BASE_URL}{path}{q}"
    try:
        with urllib.request.urlopen(url, timeout=_API_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _stock_analysis_text(symbol: str, name: str) -> str:
    """返回单只股票技术分析文本（经 API 网关，不直连 LongPort）。"""
    row = _api_get_json("/signals", {"symbol": symbol})
    if not isinstance(row, dict):
        return "[%s %s] 分析失败: API 不可用" % (symbol, name)
    try:
        px = float(row.get("latest_price", row.get("latest_close", 0.0)) or 0.0)
        ma5 = float(row.get("ma5", 0.0) or 0.0)
        ma20 = float(row.get("ma20", 0.0) or 0.0)
        rsi = float(row.get("rsi14", 50.0) or 50.0)
        trend = "上升" if ma5 > ma20 else "下降"
        rsi_s = "超卖" if rsi < 30 else ("超买" if rsi > 70 else "中性")
        rec = "偏多" if (trend == "上升" and rsi < 70) else ("偏空" if (trend == "下降" and rsi > 30) else "观望")
        lines = [
            "【%s %s】" % (symbol, name),
            "  现价: %.2f  MA5: %.2f  MA20: %.2f  趋势: %s" % (px, ma5, ma20, trend),
            "  RSI: %.2f  信号: %s  建议: %s" % (rsi, rsi_s, rec),
        ]
        return "\n".join(lines)
    except Exception as e:
        return "[%s %s] 分析失败: %s" % (symbol, name, e)


def _market_report_text() -> str:
    """市场分析摘要（经 API 网关，不直连 LongPort）。"""
    lines = ["【综合市场分析】", ""]
    try:
        a = _api_get_json("/market/analysis")
        if not isinstance(a, dict):
            raise RuntimeError("market_analysis_api_unavailable")
        ind = a["indicators"]
        fg = ind["fear_greed_index"]
        vix = ind["vix"]
        ty = ind["treasury_10y"]
        dxy = ind["dollar_index"]
        lines.append(a["market_environment"])
        lines.append("综合评分: %s/5" % a["score"])
        lines.append("策略建议: %s" % a["strategy_recommendation"])
        lines.append("情绪: %s/100   VIX: %s   10Y: %s%%   DXY: %s" % (fg["value"], vix["value"], ty["value"], dxy["value"]))
        lines.append("")
    except Exception as e:
        lines.append("获取失败: %s" % e)
        lines.append("")
    try:
        s = _api_get_json("/market/sectors", {"days": 5})
        if isinstance(s, dict) and "error" not in s:
            lines.append("板块轮动: %s" % s["rotation_phase"])
            lines.append("强势: %s  弱势: %s" % (
                "、".join(x["name"] for x in s["top_performers"][:3]),
                "、".join(x["name"] for x in s["bottom_performers"][:3]),
            ))
        lines.append("")
    except Exception:
        pass
    return "\n".join(lines)


def build_full_report() -> str:
    """市场分析 + RXRX.US / HOOD.US 股票分析，合并为一条文本。"""
    parts = [
        "===== 每小时推送 =====  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
        _market_report_text(),
        "【美股标的】",
        "",
    ]
    for symbol, name in STOCK_SYMBOLS:
        parts.append(_stock_analysis_text(symbol, name))
        parts.append("")
    parts.append("—")
    return "\n".join(parts)


def send_to_feishu(text: str) -> bool:
    """发送到飞书，返回是否至少有一个机器人发送成功。"""
    from feishu_bot import get_notification_manager
    mgr = get_notification_manager()
    results = mgr.send_text(text)
    return any(results.values())


def run_once():
    report = build_full_report()
    ok = send_to_feishu(report)
    return ok


def main():
    print("hourly_market_stocks_to_feishu: 每 %s 秒执行一次，发送市场分析 + RXRX.US / HOOD.US 到飞书" % INTERVAL_SECONDS)
    print("首次执行在 1 分钟后，之后每隔 1 小时。Ctrl+C 退出。")
    # 首次延迟 1 分钟，避免启动瞬间连续请求
    time.sleep(60)
    while True:
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ok = run_once()
            print("[%s] 发送 %s" % (ts, "成功" if ok else "失败"))
        except Exception as e:
            print("[%s] 异常: %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), e))
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
