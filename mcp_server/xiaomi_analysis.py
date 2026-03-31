"""
小米集团 (01810.HK) 行情与技术分析
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

SYMBOL = "01810.HK"
NAME = "小米集团"


def run():
    try:
        q = urllib.parse.urlencode({"symbol": SYMBOL})
        with urllib.request.urlopen(f"{_API_BASE_URL}/internal/longport/quote?{q}", timeout=_API_TIMEOUT) as resp:
            raw_quote = resp.read().decode("utf-8", errors="ignore")
            quote_row = json.loads(raw_quote) if raw_quote else {}
    except Exception as e:
        print(" [X] 行情获取失败(API):", e)
        return
    if not isinstance(quote_row, dict) or not bool(quote_row.get("available")):
        print(" [X] 未找到", SYMBOL)
        return

    last = float(quote_row.get("last", 0.0) or 0.0)
    prev = float(quote_row.get("prev_close", 0.0) or 0.0)
    high = last
    low = last
    chg_pct = (last - prev) / prev * 100 if prev else 0
    vol = 0

    try:
        s = urllib.parse.urlencode({"symbol": SYMBOL})
        with urllib.request.urlopen(f"{_API_BASE_URL}/signals?{s}", timeout=_API_TIMEOUT) as resp:
            raw_sig = resp.read().decode("utf-8", errors="ignore")
            sig_row = json.loads(raw_sig) if raw_sig else {}
    except Exception as e:
        print(" [X] 信号获取失败(API):", e)
        sig_row = {}

    if not isinstance(sig_row, dict):
        ma5 = ma20 = rsi = None
        trend = rsi_signal = rec = "数据不足"
    else:
        ma5 = float(sig_row.get("ma5", 0.0) or 0.0)
        ma20 = float(sig_row.get("ma20", 0.0) or 0.0)
        rsi = float(sig_row.get("rsi14", 50.0) or 50.0)
        trend = "上升" if ma5 and ma20 and ma5 > ma20 else "下降"
        rsi_signal = "超卖" if rsi is not None and rsi < 30 else ("超买" if rsi is not None and rsi > 70 else "中性")
        if trend == "上升" and (rsi is None or rsi < 70):
            rec = "偏多"
        elif trend == "下降" and (rsi is None or rsi > 30):
            rec = "偏空"
        else:
            rec = "观望"

    # 输出
    lines = [
        "",
        "========== " + NAME + " (" + SYMBOL + ") ==========",
        "",
        "【实时行情】",
        "  现价: %.2f  昨收: %.2f  涨跌: %+.2f%%" % (last, prev, chg_pct),
        "  最高: %.2f  最低: %.2f  成交量: %s" % (high, low, vol),
        "",
        "【技术指标】",
    ]
    if ma5 is not None and ma20 is not None:
        lines.append("  MA5: %.2f  MA20: %.2f  趋势: %s" % (ma5, ma20, trend))
    if rsi is not None:
        lines.append("  RSI(14): %.1f  %s" % (rsi, rsi_signal))
    lines.append("  短线信号: " + rec)
    lines.append("")
    lines.append("【走势简析】")
    if trend == "上升" and (rsi is None or rsi < 70):
        lines.append("  均线多头排列(MA5>MA20)，RSI未超买，短线技术面偏多。")
        lines.append("  若量能配合，可关注前高与整数关口压力。")
    elif trend == "下降" and (rsi is not None and rsi > 30):
        lines.append("  均线空头排列，RSI未超卖，短线技术面偏空。")
        lines.append("  可关注下方均线或前低支撑，再观察企稳信号。")
    elif rsi is not None and rsi >= 70:
        lines.append("  RSI 进入超买区，短线追高风险加大，可考虑获利了结或设好止损。")
    elif rsi is not None and rsi <= 30:
        lines.append("  RSI 进入超卖区，若在重要支撑位可关注反弹机会，控制仓位。")
    else:
        lines.append("  趋势与 RSI 中性，建议观望或结合大盘与消息面再决策。")
    lines.append("")
    lines.append("（以上为技术面参考，不构成投资建议。）")
    lines.append("")

    text = "\n".join(lines)
    print(text)
    return text


if __name__ == "__main__":
    run()
