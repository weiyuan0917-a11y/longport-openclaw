"""
发送今日市场分析到飞书/钉钉
在项目根目录或 mcp_server 下运行：
  python mcp_server/send_market_analysis_to_feishu.py
  或在 mcp_server 下：python send_market_analysis_to_feishu.py
"""
import sys
import os
import json
import urllib.parse
import urllib.request

# 确保可导入 mcp_server 内模块
_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)
root = os.path.dirname(_dir)
if root not in sys.path:
    sys.path.insert(0, root)

from datetime import datetime
from feishu_bot import get_notification_manager

_API_BASE_URL = str(os.getenv("API_BASE_URL", "http://127.0.0.1:8000")).strip().rstrip("/")
_API_TIMEOUT = max(1.0, float(os.getenv("API_TIMEOUT_SECONDS", "10")))


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


def format_market_report() -> str:
    """获取并格式化为今日市场分析文本"""
    lines = [
        "📊 今日市场分析",
        "=" * 40,
        "",
    ]
    try:
        analysis = _api_get_json("/market/analysis")
        if not isinstance(analysis, dict):
            raise RuntimeError("market_analysis_api_unavailable")
        ind = analysis["indicators"]
        fg = ind["fear_greed_index"]
        vix = ind["vix"]
        ty = ind["treasury_10y"]
        dxy = ind["dollar_index"]

        lines.append("【综合判断】")
        lines.append(analysis["market_environment"])
        lines.append(f"综合评分: {analysis['score']}/5")
        lines.append("")
        lines.append("【策略建议】")
        lines.append(analysis["strategy_recommendation"])
        lines.append("")
        lines.append("【核心指标】")
        lines.append(f"• 情绪指数: {fg['value']}/100 ({fg['level']})")
        lines.append(f"• VIX: {vix['value']} ({vix.get('interpretation', '')[:20]}...)")
        lines.append(f"• 10Y国债: {ty['value']}%")
        lines.append(f"• 美元指数: {dxy['value']}")
        lines.append("")
    except Exception as e:
        lines.append(f"【综合分析】获取失败: {e}")
        lines.append("")

    try:
        sector = _api_get_json("/market/sectors", {"days": 5})
        if not isinstance(sector, dict) or "error" in sector:
            lines.append("【板块轮动】暂不可用")
        else:
            lines.append("【板块轮动（近5日）】")
            lines.append(sector["rotation_phase"])
            lines.append("强势: " + "、".join(s["name"] for s in sector["top_performers"][:3]))
            lines.append("弱势: " + "、".join(s["name"] for s in sector["bottom_performers"][:3]))
        lines.append("")
    except Exception as e:
        lines.append(f"【板块轮动】获取失败: {e}")
        lines.append("")

    lines.append("—")
    lines.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return "\n".join(lines)


def main():
    text = format_market_report()
    manager = get_notification_manager()
    results = manager.send_text(text)
    sent = [k for k, v in results.items() if v]
    if not sent:
        # 避免 Windows GBK 控制台对 emoji 报错
        print(" [X] 发送失败，请检查 notification_config.json 是否已配置飞书/钉钉 Webhook。")
        sys.exit(1)
    print(" [OK] 今日市场分析已发送到:", ", ".join(sent))
    preview = text[:500] + ("..." if len(text) > 500 else "")
    try:
        print("\n" + preview)
    except UnicodeEncodeError:
        print("\n" + preview.encode("gbk", errors="replace").decode("gbk"))


if __name__ == "__main__":
    main()
