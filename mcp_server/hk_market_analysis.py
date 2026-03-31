"""
今日港股行情分析
使用 LongPort 获取恒生指数、恒生科技及主要港股实时行情并汇总。
运行：在项目根目录 PYTHONPATH=D:\\longport-openclaw python mcp_server/hk_market_analysis.py
"""
import sys
import os
import json
import urllib.parse
import urllib.request

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)
root = os.path.dirname(_dir)
if root not in sys.path:
    sys.path.insert(0, root)

_API_BASE_URL = str(os.getenv("API_BASE_URL", "http://127.0.0.1:8000")).strip().rstrip("/")
_API_TIMEOUT = max(1.0, float(os.getenv("API_TIMEOUT_SECONDS", "10")))

# 港股标的：指数 + 部分蓝筹/科技
HK_SYMBOLS = [
    ("HSI.HK", "恒生指数"),
    ("HSTECH.HK", "恒生科技指数"),
    ("00700.HK", "腾讯控股"),
    ("09988.HK", "阿里巴巴"),
    ("03690.HK", "美团"),
    ("09618.HK", "京东集团"),
    ("09868.HK", "小鹏汽车"),
    ("09999.HK", "网易"),
]


def run():
    by_symbol: dict[str, dict] = {}
    for symbol, _name in HK_SYMBOLS:
        try:
            q = urllib.parse.urlencode({"symbol": symbol})
            with urllib.request.urlopen(f"{_API_BASE_URL}/internal/longport/quote?{q}", timeout=_API_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                row = json.loads(raw) if raw else {}
            if isinstance(row, dict) and bool(row.get("available")):
                by_symbol[symbol] = row
        except Exception:
            continue

    if not by_symbol:
        print(" [X] API 行情请求失败或无可用数据")
        return
    lines = [
        "",
        "========== 今日港股行情 ==========",
        "",
    ]
    for symbol, name in HK_SYMBOLS:
        row = by_symbol.get(symbol)
        if not row:
            lines.append(f"  {name} ({symbol})  -- 暂无数据")
            continue
        try:
            last = float(row.get("last", 0.0) or 0.0)
            prev = float(row.get("prev_close", 0.0) or 0.0)
            chg_pct = (last - prev) / prev * 100 if prev else 0
            high = last
            low = last
            vol = 0
            lines.append(f"  {name} ({symbol})")
            lines.append(f"    现价: {last:.2f}  昨收: {prev:.2f}  涨跌: {chg_pct:+.2f}%")
            lines.append(f"    最高: {high:.2f}  最低: {low:.2f}  成交量: {vol}")
            lines.append("")
        except Exception as e:
            lines.append(f"  {name} ({symbol}) 解析异常: {e}")
            lines.append("")

    # 简要总结
    valid = [by_symbol.get(s[0]) for s in HK_SYMBOLS if by_symbol.get(s[0])]
    if valid:
        ups = sum(1 for x in valid if float(x.get("last", 0.0) or 0.0) >= float(x.get("prev_close", 0.0) or 0.0))
        downs = len(valid) - ups
        lines.append("---------- 小结 ----------")
        lines.append(f"  统计: {len(valid)} 个标的有数据，上涨 {ups} / 下跌 {downs}")
        lines.append("")

    text = "\n".join(lines)
    print(text)
    return text


if __name__ == "__main__":
    run()
