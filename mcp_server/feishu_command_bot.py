"""
feishu_command_bot.py - 飞书指令机器人（WebSocket 长连接模式）
无需公网 IP，bot 主动连接飞书服务器接收消息。

启动方式：
  cd D:\\longport-openclaw
  $env:PYTHONPATH="D:\\longport-openclaw"
  python mcp_server/feishu_command_bot.py

依赖：lark-oapi, longport
"""
import sys
import os
import re
import json
import time
import logging
import threading
import atexit
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)
_root = os.path.dirname(_dir)
if _root not in sys.path:
    sys.path.insert(0, _root)

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from config.notification_settings import resolve_feishu_app_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("feishu_bot")
_PID_FILE = os.path.join(_dir, ".feishu_bot.pid")


def _write_pid_file() -> None:
    try:
        with open(_PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _remove_pid_file() -> None:
    try:
        if os.path.exists(_PID_FILE):
            os.remove(_PID_FILE)
    except Exception:
        pass

# ============================================================
# 配置加载
# ============================================================

NOTIFICATION_CONFIG_PATH = os.path.join(_dir, "notification_config.json")


def _load_feishu_app_config() -> dict:
    cfg = resolve_feishu_app_config(NOTIFICATION_CONFIG_PATH)
    if not cfg.get("app_id") or not cfg.get("app_secret"):
        log.warning("飞书配置不完整：请配置 FEISHU_APP_ID/FEISHU_APP_SECRET 或 notification_config.json")
    return cfg


FEISHU_APP = _load_feishu_app_config()
APP_ID = FEISHU_APP.get("app_id", "")
APP_SECRET = FEISHU_APP.get("app_secret", "")
SCHEDULED_CHAT_ID = FEISHU_APP.get("scheduled_chat_id", "")
USE_API_PROXY = str(os.getenv("FEISHU_BOT_USE_API_PROXY", "true")).strip().lower() in {"1", "true", "yes", "on"}
API_BASE_URL = str(os.getenv("FEISHU_BOT_API_BASE_URL", "http://127.0.0.1:8000")).strip().rstrip("/")
API_TIMEOUT_SECONDS = max(1.0, float(os.getenv("FEISHU_BOT_API_TIMEOUT_SECONDS", "8")))


def _api_get_json(path: str, params: dict | None = None) -> dict | None:
    try:
        q = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{API_BASE_URL}{path}{q}"
        with urllib.request.urlopen(url, timeout=API_TIMEOUT_SECONDS) as resp:
            if int(getattr(resp, "status", 200) or 200) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _api_post_json(path: str, payload: dict | None = None) -> tuple[bool, dict]:
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{API_BASE_URL}{path}", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=max(API_TIMEOUT_SECONDS, 12.0)) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
            return True, (data if isinstance(data, dict) else {})
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
            if isinstance(data, dict):
                return False, data
        except Exception:
            pass
        return False, {"error": f"http_{int(getattr(e, 'code', 500) or 500)}"}
    except Exception as e:
        return False, {"error": str(e)}

# ============================================================
# LongPort 初始化
# ============================================================

_lp_app_key = os.getenv("LONGPORT_APP_KEY")
_lp_app_secret = os.getenv("LONGPORT_APP_SECRET")
_lp_access_token = os.getenv("LONGPORT_ACCESS_TOKEN")
if not _lp_app_key:
    try:
        from config.live_settings import live_settings
        _lp_app_key = live_settings.LONGPORT_APP_KEY
        _lp_app_secret = live_settings.LONGPORT_APP_SECRET
        _lp_access_token = live_settings.LONGPORT_ACCESS_TOKEN
    except Exception:
        pass

quote_ctx = None
trade_ctx = None
if _lp_app_key and not USE_API_PROXY:
    try:
        from longport.openapi import Config, QuoteContext, TradeContext
        _lp_cfg = Config(
            app_key=_lp_app_key,
            app_secret=_lp_app_secret,
            access_token=_lp_access_token,
            enable_overnight=True,
            enable_print_quote_packages=False,
        )
        quote_ctx = QuoteContext(_lp_cfg)
        trade_ctx = TradeContext(_lp_cfg)
        log.info("LongPort 初始化成功")
    except Exception as e:
        log.warning("LongPort 初始化失败: %s", e)

# ============================================================
# lark-oapi Client（用于发送消息）
# ============================================================

lark_client: lark.Client | None = None
if APP_ID and APP_SECRET:
    lark_client = (
        lark.Client.builder()
        .app_id(APP_ID)
        .app_secret(APP_SECRET)
        .log_level(lark.LogLevel.INFO)
        .build()
    )


def _reply_to_message(message_id: str, text: str) -> bool:
    """通过 message_id 回复消息"""
    if not lark_client:
        log.error("lark_client 未初始化，无法回复")
        return False
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = lark_client.im.v1.message.reply(req)
    if not resp.success():
        log.error("回复失败: code=%s msg=%s", resp.code, resp.msg)
        return False
    return True


def _send_to_chat(chat_id: str, text: str) -> bool:
    """通过 chat_id 发送消息（备用）"""
    if not lark_client:
        log.error("lark_client 未初始化，无法发送")
        return False
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = lark_client.im.v1.message.create(req)
    if not resp.success():
        log.error("发送失败: code=%s msg=%s", resp.code, resp.msg)
        return False
    return True


# ============================================================
# 去重
# ============================================================

_processed_events: dict[str, float] = {}
_EVENT_TTL = 300


def _is_duplicate(event_id: str) -> bool:
    now = time.time()
    expired = [k for k, v in _processed_events.items() if now - v > _EVENT_TTL]
    for k in expired:
        del _processed_events[k]
    if event_id in _processed_events:
        return True
    _processed_events[event_id] = now
    return False


# ============================================================
# 指令执行器
# ============================================================

def cmd_help() -> str:
    return (
        "支持的指令：\n"
        "  行情 <代码>       - 实时行情，如：行情 AAPL.US\n"
        "  分析 <代码>       - 技术分析，如：分析 RXRX.US\n"
        "  买入 <代码> <数量> [价格] - 买入股票，如：买入 01810.HK 1000\n"
        "  卖出 <代码> <数量> [价格] - 卖出股票\n"
        "  持仓              - 查看所有持仓\n"
        "  订单 [all|active|filled|cancelled] - 查看今日订单\n"
        "  取消 <订单ID>     - 取消订单\n"
        "  账户              - 查看账户信息\n"
        "  市场分析          - 综合市场分析\n"
        "  板块轮动          - 板块轮动分析\n"
        "  止损扫描          - 扫描持仓止损状态\n"
        "  风控              - 查看风控参数\n"
        "  帮助              - 显示本帮助\n"
    )


_ET = ZoneInfo("America/New_York")
_QUOTE_TS_SOURCE_TZ = ZoneInfo(os.getenv("QUOTE_TS_SOURCE_TZ", "Asia/Shanghai"))


def _as_et_datetime(raw):
    """将时间戳转换到美东时区（支持 naive datetime / iso string）。"""
    if raw is None:
        return None
    dt = None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        # LongPort 常见为无时区时间，按 UTC+8 来源解释（可通过环境变量覆盖）。
        dt = dt.replace(tzinfo=_QUOTE_TS_SOURCE_TZ)
    return dt.astimezone(_ET)


def _extract_quote_ts(quote_obj):
    for attr in ("timestamp", "trade_timestamp", "updated_at", "time"):
        if hasattr(quote_obj, attr):
            ts = _as_et_datetime(getattr(quote_obj, attr))
            if ts is not None:
                return ts
    return None


def _session_kind_et(now_et: datetime) -> str:
    t = now_et.timetz().replace(tzinfo=None)
    if dt_time(4, 0) <= t < dt_time(9, 30):
        return "盘前"
    if dt_time(9, 30) <= t < dt_time(16, 0):
        return "盘中"
    if dt_time(16, 0) <= t < dt_time(20, 0):
        return "盘后"
    return "夜盘"


def _is_fresh_for_session(kind: str, quote_ts_et: datetime | None, now_et: datetime) -> bool:
    if quote_ts_et is None:
        return False
    today = now_et.date()
    t = quote_ts_et.timetz().replace(tzinfo=None)
    if kind == "盘前":
        return quote_ts_et.date() == today and dt_time(4, 0) <= t < dt_time(9, 30)
    if kind == "盘中":
        return quote_ts_et.date() == today and dt_time(9, 30) <= t < dt_time(16, 0)
    if kind == "盘后":
        return quote_ts_et.date() == today and dt_time(16, 0) <= t < dt_time(20, 0)
    if kind == "夜盘":
        now_t = now_et.timetz().replace(tzinfo=None)
        if now_t < dt_time(4, 0):
            start = datetime.combine(today - timedelta(days=1), dt_time(20, 0), tzinfo=_ET)
            end = datetime.combine(today, dt_time(4, 0), tzinfo=_ET)
        else:
            start = datetime.combine(today, dt_time(20, 0), tzinfo=_ET)
            end = datetime.combine(today + timedelta(days=1), dt_time(4, 0), tzinfo=_ET)
        return start <= quote_ts_et < end
    return False


def _get_realtime_price_feishu(q):
    """按美东时段优先取价，并做时间戳新鲜度校验。"""
    now_et = datetime.now(timezone.utc).astimezone(_ET)
    session = _session_kind_et(now_et)
    candidates = {
        "盘前": getattr(q, "pre_market_quote", None),
        "盘后": getattr(q, "post_market_quote", None),
        "夜盘": getattr(q, "overnight_quote", None),
        "盘中": q,
    }
    preferred = {
        "盘前": ["盘前", "盘中", "夜盘", "盘后"],
        "盘中": ["盘中", "盘前", "盘后", "夜盘"],
        "盘后": ["盘后", "盘中", "夜盘", "盘前"],
        "夜盘": ["夜盘", "盘后", "盘中", "盘前"],
    }[session]

    # 第一轮：按时段+时间戳严格匹配
    for kind in preferred:
        obj = candidates.get(kind)
        if not obj or not getattr(obj, "last_done", None):
            continue
        if kind == "盘中":
            return float(obj.last_done), kind
        ts = _extract_quote_ts(obj)
        if _is_fresh_for_session(kind, ts, now_et):
            return float(obj.last_done), kind

    # 第二轮：兜底取任意可用最新价，避免空值
    for kind in preferred:
        obj = candidates.get(kind)
        if obj and getattr(obj, "last_done", None):
            return float(obj.last_done), kind
    return float(q.last_done), "盘中"


def cmd_market_data(symbol: str) -> str:
    if USE_API_PROXY:
        row = _api_get_json("/internal/longport/quote", {"symbol": symbol})
        if not isinstance(row, dict) or not bool(row.get("available")):
            return f"未找到 {symbol}"
        last = float(row.get("last", 0.0) or 0.0)
        prev = float(row.get("prev_close", 0.0) or 0.0)
        chg = float(row.get("change_pct", 0.0) or 0.0)
        ptype = str(row.get("price_type", "盘中") or "盘中")
        return (
            f"{symbol} 实时行情 ({ptype})\n"
            f"  现价: {last:.2f}  昨收: {prev:.2f}  涨跌: {chg:+.2f}%\n"
            f"  来源: API代理"
        )
    if not quote_ctx:
        return "LongPort 未初始化，无法获取行情"
    try:
        qs = quote_ctx.quote([symbol])
        if not qs:
            return f"未找到 {symbol}"
        q = qs[0]
        
        # 使用实时价格（优先盘前盘后）
        last, price_type = _get_realtime_price_feishu(q)
        prev = float(q.prev_close)
        chg = ((last - prev) / prev * 100) if prev else 0
        
        # 根据价格类型选择对应的高低价
        if price_type == '盘后' and hasattr(q, 'post_market_quote') and q.post_market_quote:
            high = float(q.post_market_quote.high) if hasattr(q.post_market_quote, 'high') else float(q.high)
            low = float(q.post_market_quote.low) if hasattr(q.post_market_quote, 'low') else float(q.low)
        elif price_type == '盘前' and hasattr(q, 'pre_market_quote') and q.pre_market_quote:
            high = float(q.pre_market_quote.high) if hasattr(q.pre_market_quote, 'high') else float(q.high)
            low = float(q.pre_market_quote.low) if hasattr(q.pre_market_quote, 'low') else float(q.low)
        else:
            high = float(q.high)
            low = float(q.low)
        
        return (
            f"{symbol} 实时行情 ({price_type})\n"
            f"  现价: {last:.2f}  昨收: {prev:.2f}  涨跌: {chg:+.2f}%\n"
            f"  最高: {high:.2f}  最低: {low:.2f}\n"
            f"  成交量: {q.volume}  状态: {q.trade_status}"
        )
    except Exception as e:
        return f"获取行情失败: {e}"


def cmd_analyze(symbol: str) -> str:
    if USE_API_PROXY:
        row = _api_get_json("/signals", {"symbol": symbol})
        if not isinstance(row, dict):
            return f"分析失败: 无法获取 {symbol} 信号数据"
        rsi = float(row.get("rsi14", 50.0) or 50.0)
        ma5 = float(row.get("ma5", 0.0) or 0.0)
        ma20 = float(row.get("ma20", 0.0) or 0.0)
        px = float(row.get("latest_price", row.get("latest_close", 0.0)) or 0.0)
        trend = "上升" if ma5 > ma20 else "下降"
        rsi_s = "超卖" if rsi < 30 else ("超买" if rsi > 70 else "中性")
        rec = "买入" if (trend == "上升" and rsi < 70) else ("卖出" if (trend == "下降" and rsi > 30) else "观望")
        return (
            f"{symbol} 技术分析\n"
            f"  现价: {px:.2f}\n"
            f"  MA5: {ma5:.2f}  MA20: {ma20:.2f}  趋势: {trend}\n"
            f"  RSI: {rsi:.2f}  信号: {rsi_s}\n"
            f"  建议: {rec}"
        )
    if not quote_ctx:
        return "LongPort 未初始化，无法分析"
    try:
        from longport.openapi import Period, AdjustType, TradeSessions
        import numpy as np
        end = date.today()
        start = end - timedelta(days=90)
        cs = quote_ctx.history_candlesticks_by_date(
            symbol=symbol,
            period=Period.Day,
            adjust_type=AdjustType.ForwardAdjust,
            start=start,
            end=end,
            trade_sessions=TradeSessions.All,
        )
        if not cs or len(cs) < 20:
            return f"{symbol} 数据不足（需至少 20 日 K 线）"
        closes = [float(c.close) for c in cs]
        ma5 = round(sum(closes[-5:]) / 5, 2)
        ma20 = round(sum(closes[-20:]) / 20, 2)
        diff = np.diff(closes[-15:])
        g, l = diff[diff > 0], -diff[diff < 0]
        ag = float(np.mean(g)) if len(g) else 0
        al = float(np.mean(l)) if len(l) else 0
        rsi = round(100 - 100 / (1 + ag / al), 2) if al else 100
        trend = "上升" if ma5 > ma20 else "下降"
        rsi_s = "超卖" if rsi < 30 else ("超买" if rsi > 70 else "中性")
        rec = "买入" if (trend == "上升" and rsi < 70) else ("卖出" if (trend == "下降" and rsi > 30) else "观望")
        return (
            f"{symbol} 技术分析\n"
            f"  现价: {closes[-1]:.2f}\n"
            f"  MA5: {ma5}  MA20: {ma20}  趋势: {trend}\n"
            f"  RSI: {rsi}  信号: {rsi_s}\n"
            f"  建议: {rec}"
        )
    except Exception as e:
        return f"分析失败: {e}"


def cmd_submit_order(action: str, symbol: str, quantity: int, price: float = None) -> str:
    if USE_API_PROXY:
        payload = {"action": str(action).lower(), "symbol": str(symbol).upper(), "quantity": int(quantity)}
        if price is not None:
            payload["price"] = float(price)
        ok, row = _api_post_json("/trade/order", payload)
        if not ok:
            return f"下单失败: {row.get('detail') or row.get('error') or row}"
        order_id = row.get("order_id", "")
        price_str = f"{price}" if price is not None else "市价"
        return (
            f"订单已提交\n"
            f"  订单ID: {order_id}\n"
            f"  {symbol} {'买入' if action == 'buy' else '卖出'} {quantity} 股 @ {price_str}\n"
            f"  通道: API代理"
        )
    if not trade_ctx:
        return "LongPort 未初始化，无法下单"
    try:
        from longport.openapi import OrderSide, OrderType, TimeInForceType
        from decimal import Decimal
        from risk_manager import get_manager

        cp = float(price) if price else 0
        if not cp and action == "buy":
            try:
                qs = quote_ctx.quote([symbol])
                cp = _get_realtime_price_feishu(qs[0])[0] if qs else 0
            except Exception:
                pass
        if cp and action == "buy":
            bl = trade_ctx.account_balance()
            b = bl[0] if bl else None
            ta = float(b.net_assets) if b else 0
            ac = float(b.buy_power) if b else 0
            ev = 0.0
            for ch in trade_ctx.stock_positions().channels:
                for p in ch.positions:
                    if p.symbol == symbol:
                        try:
                            q = quote_ctx.quote([symbol])
                            if q:
                                cur, _ = _get_realtime_price_feishu(q[0])
                            else:
                                cur = float(p.cost_price)
                        except Exception:
                            cur = float(p.cost_price)
                        ev = cur * float(p.quantity)
            rr = get_manager().full_check_before_order(
                symbol=symbol, action=action, quantity=quantity, price=cp,
                total_assets=ta, available_cash=ac, existing_position_value=ev,
            )
            if not rr["passed"]:
                blocks = "; ".join(blk["reason"] for blk in rr["blocks"])
                return f"风控拦截: {blocks}"

        side = OrderSide.Buy if action == "buy" else OrderSide.Sell
        resp = trade_ctx.submit_order(
            symbol=symbol,
            order_type=OrderType.LO if price else OrderType.MO,
            side=side,
            submitted_quantity=quantity,
            time_in_force=TimeInForceType.Day,
            **({} if not price else {"submitted_price": Decimal(str(price))}),
        )
        price_str = f"{price}" if price else "市价"
        return (
            f"订单已提交\n"
            f"  订单ID: {resp.order_id}\n"
            f"  {symbol} {'买入' if action == 'buy' else '卖出'} {quantity} 股 @ {price_str}\n"
            f"  风控: 通过"
        )
    except Exception as e:
        return f"下单失败: {e}"


def cmd_positions() -> str:
    if USE_API_PROXY:
        row = _api_get_json("/trade/positions")
        items = row.get("positions") if isinstance(row, dict) else None
        if not isinstance(items, list):
            return "获取持仓失败: API 返回异常"
        if not items:
            return "当前无持仓"
        lines = []
        for pos in items:
            if not isinstance(pos, dict):
                continue
            qty = float(pos.get("quantity", 0.0) or 0.0)
            cp = float(pos.get("cost_price", 0.0) or 0.0)
            cur = float(pos.get("current_price", 0.0) or 0.0)
            cost = cp * qty
            val = cur * qty
            pnl = val - cost
            pp = (pnl / cost * 100) if cost else 0.0
            lines.append(
                f"  {pos.get('symbol','-')}  {int(qty)}股  成本:{cp:.2f}  现价:{cur:.2f}  盈亏:{pnl:+.2f} ({pp:+.2f}%)"
            )
        return "持仓列表：\n" + "\n".join(lines) if lines else "当前无持仓"
    if not trade_ctx:
        return "LongPort 未初始化"
    try:
        lines = []
        for ch in trade_ctx.stock_positions().channels:
            for pos in ch.positions:
                try:
                    q = quote_ctx.quote([pos.symbol])
                    cur = _get_realtime_price_feishu(q[0])[0] if q else 0
                except Exception:
                    cur = 0
                cost = float(pos.cost_price) * float(pos.quantity)
                val = cur * float(pos.quantity)
                pnl = val - cost
                pp = (pnl / cost * 100) if cost else 0
                lines.append(
                    f"  {pos.symbol}  {int(float(pos.quantity))}股  "
                    f"成本:{float(pos.cost_price):.2f}  现价:{cur:.2f}  "
                    f"盈亏:{pnl:+.2f} ({pp:+.2f}%)"
                )
        if not lines:
            return "当前无持仓"
        return "持仓列表：\n" + "\n".join(lines)
    except Exception as e:
        return f"获取持仓失败: {e}"


def cmd_orders(status: str = "all") -> str:
    if USE_API_PROXY:
        row = _api_get_json("/trade/orders", {"status": status})
        items = row.get("orders") if isinstance(row, dict) else None
        if not isinstance(items, list):
            return "获取订单失败: API 返回异常"
        if not items:
            return "无订单"
        lines = []
        for o in items:
            if not isinstance(o, dict):
                continue
            p = o.get("price", "市价")
            lines.append(
                f"  {o.get('order_id','-')}  {o.get('symbol','-')}  {o.get('side','-')}  {o.get('quantity','-')}股  价:{p}  状态:{o.get('status','-')}"
            )
        return "今日订单：\n" + "\n".join(lines)
    if not trade_ctx:
        return "LongPort 未初始化"
    try:
        allowed = {
            "active": {"New", "PartialFilled"},
            "filled": {"Filled"},
            "cancelled": {"Canceled"},
        }.get(status)
        lines = []
        for o in trade_ctx.today_orders():
            s = str(o.status)
            if allowed and s not in allowed:
                continue
            p = float(o.price) if o.price else "市价"
            lines.append(f"  {o.order_id}  {o.symbol}  {o.side}  {o.quantity}股  价:{p}  状态:{s}")
        if not lines:
            return "无订单"
        return "今日订单：\n" + "\n".join(lines)
    except Exception as e:
        return f"获取订单失败: {e}"


def cmd_cancel_order(order_id: str) -> str:
    if USE_API_PROXY:
        ok, row = _api_post_json(f"/trade/order/{order_id}/cancel", {})
        if not ok:
            return f"取消失败: {row.get('detail') or row.get('error') or row}"
        return f"订单 {order_id} 已取消"
    if not trade_ctx:
        return "LongPort 未初始化"
    try:
        trade_ctx.cancel_order(order_id)
        return f"订单 {order_id} 已取消"
    except Exception as e:
        return f"取消失败: {e}"


def cmd_account() -> str:
    if USE_API_PROXY:
        acc = _api_get_json("/trade/account")
        pos = _api_get_json("/trade/positions")
        if not isinstance(acc, dict):
            return "获取账户失败: API 返回异常"
        items = pos.get("positions") if isinstance(pos, dict) else []
        cnt = 0
        val = 0.0
        if isinstance(items, list):
            for p in items:
                if not isinstance(p, dict):
                    continue
                cnt += 1
                val += float(p.get("quantity", 0.0) or 0.0) * float(p.get("current_price", 0.0) or 0.0)
        return (
            f"账户信息\n"
            f"  总资产: {float(acc.get('net_assets', 0.0) or 0.0):,.2f}\n"
            f"  可用现金: {float(acc.get('buy_power', 0.0) or 0.0):,.2f}\n"
            f"  货币: {acc.get('currency', '-')}\n"
            f"  持仓: {cnt} 只  持仓总值: {val:,.2f}"
        )
    if not trade_ctx:
        return "LongPort 未初始化"
    try:
        bl = trade_ctx.account_balance()
        if not bl:
            return "账户信息为空"
        b = bl[0]
        pos = trade_ctx.stock_positions()
        cnt, val = 0, 0.0
        for ch in pos.channels:
            for p in ch.positions:
                cnt += 1
                val += float(p.quantity) * float(p.cost_price)
        return (
            f"账户信息\n"
            f"  总资产: {float(b.net_assets):,.2f}\n"
            f"  可用现金: {float(b.buy_power):,.2f}\n"
            f"  货币: {b.currency}\n"
            f"  持仓: {cnt} 只  持仓总值: {val:,.2f}"
        )
    except Exception as e:
        return f"获取账户失败: {e}"


def cmd_market_analysis() -> str:
    """手动「市场分析」指令也返回完整多市场报告"""
    if USE_API_PROXY:
        row = _api_get_json("/market/analysis")
        if isinstance(row, dict) and row:
            ind = row.get("indicators") if isinstance(row.get("indicators"), dict) else {}
            fg = ind.get("fear_greed_index") if isinstance(ind.get("fear_greed_index"), dict) else {}
            vix = ind.get("vix") if isinstance(ind.get("vix"), dict) else {}
            ty = ind.get("treasury_10y") if isinstance(ind.get("treasury_10y"), dict) else {}
            dxy = ind.get("dollar_index") if isinstance(ind.get("dollar_index"), dict) else {}
            news = ind.get("news_sentiment") if isinstance(ind.get("news_sentiment"), dict) else {}
            crypto = ind.get("crypto_risk") if isinstance(ind.get("crypto_risk"), dict) else {}
            source = str(row.get("data_source", "unknown"))
            cache_age = row.get("cache_age_seconds")
            if source == "cache" and isinstance(cache_age, int):
                source_text = f"{source} ({cache_age}s)"
            else:
                source_text = source
            return (
                "市场分析（API代理）\n"
                f"  市场环境: {row.get('market_environment', '-')}\n"
                f"  综合评分: {row.get('score', '-')}/5\n"
                f"  策略建议: {row.get('strategy_recommendation', '-')}\n"
                f"  情绪指数: {fg.get('value', '-')}/100 ({fg.get('level', '-')})\n"
                f"  VIX: {vix.get('value', '-')}  10Y国债: {ty.get('value', '-')}%  美元指数: {dxy.get('value', '-')}\n"
                f"  新闻情绪: {news.get('level', '-')} ({news.get('score', '-')})\n"
                f"  风险资产温度: {crypto.get('level', '-')} ({crypto.get('avg_change_24h', '-') }%)\n"
                f"  数据源: {source_text}"
            )
        # 代理返回空时，回退本地拼装报告，避免用户只拿到占位文案。
        try:
            return _build_full_report()
        except Exception:
            return "市场分析失败: API 返回为空"
    try:
        return _build_full_report()
    except Exception as e:
        return f"市场分析失败: {e}"


def cmd_sector_rotation() -> str:
    if USE_API_PROXY:
        s = _api_get_json("/market/sectors", {"days": 5})
        if not isinstance(s, dict):
            return "板块分析失败: API 返回异常"
        if "error" in s:
            return f"板块轮动: {s['error']}"
        top = ", ".join(f"{x['name']}({x['change_pct']:+.2f}%)" for x in (s.get("top_performers") or [])[:3])
        bot = ", ".join(f"{x['name']}({x['change_pct']:+.2f}%)" for x in (s.get("bottom_performers") or [])[:3])
        return (
            f"板块轮动（近5日）\n"
            f"  {s.get('rotation_phase','-')}\n"
            f"  强势: {top or '-'}\n"
            f"  弱势: {bot or '-'}"
        )
    try:
        from market_analysis import get_sector_rotation
        s = get_sector_rotation(days=5)
        if "error" in s:
            return f"板块轮动: {s['error']}"
        top = ", ".join(f"{x['name']}({x['change_pct']:+.2f}%)" for x in s["top_performers"][:3])
        bot = ", ".join(f"{x['name']}({x['change_pct']:+.2f}%)" for x in s["bottom_performers"][:3])
        return (
            f"板块轮动（近5日）\n"
            f"  {s['rotation_phase']}\n"
            f"  强势: {top}\n"
            f"  弱势: {bot}"
        )
    except Exception as e:
        return f"板块分析失败: {e}"


def cmd_scan_stop_loss() -> str:
    if USE_API_PROXY:
        try:
            from risk_manager import get_manager

            mgr = get_manager()
            row = _api_get_json("/trade/positions")
            items = row.get("positions") if isinstance(row, dict) else None
            if not isinstance(items, list) or not items:
                return "无持仓，无需扫描"
            lines = []
            for pos in items:
                if not isinstance(pos, dict):
                    continue
                cur = float(pos.get("current_price", 0.0) or 0.0)
                if cur <= 0:
                    continue
                check = mgr.check_stop_loss(
                    symbol=str(pos.get("symbol", "")),
                    cost_price=float(pos.get("cost_price", 0.0) or 0.0),
                    current_price=cur,
                    quantity=float(pos.get("quantity", 0.0) or 0.0),
                )
                tag = "!! 触发止损" if check.should_stop else "正常"
                lines.append(
                    f"  {check.symbol}  浮亏:{check.loss_pct * 100:.2f}%  止损线:{check.threshold_pct * 100:.2f}%  {tag}"
                )
            return "止损扫描：\n" + "\n".join(lines) if lines else "无持仓，无需扫描"
        except Exception as e:
            return f"扫描失败: {e}"
    if not trade_ctx or not quote_ctx:
        return "LongPort 未初始化"
    try:
        from risk_manager import get_manager
        mgr = get_manager()
        lines = []
        for ch in trade_ctx.stock_positions().channels:
            for pos in ch.positions:
                try:
                    q = quote_ctx.quote([pos.symbol])
                    if q:
                        cur, price_type = _get_realtime_price_feishu(q[0])
                    else:
                        cur = 0
                except Exception:
                    cur = 0
                if cur > 0:
                    check = mgr.check_stop_loss(
                        symbol=pos.symbol,
                        cost_price=float(pos.cost_price),
                        current_price=cur,
                        quantity=float(pos.quantity),
                    )
                    tag = "!! 触发止损" if check.should_stop else "正常"
                    lines.append(
                        f"  {check.symbol}  浮亏:{check.loss_pct * 100:.2f}%  "
                        f"止损线:{check.threshold_pct * 100:.2f}%  {tag}"
                    )
        if not lines:
            return "无持仓，无需扫描"
        return "止损扫描：\n" + "\n".join(lines)
    except Exception as e:
        return f"扫描失败: {e}"


def cmd_risk_config() -> str:
    try:
        from risk_manager import load_config
        cfg = load_config()
        d = cfg.to_dict()
        lines = [f"  {k}: {v}" for k, v in d.items()]
        return "风控参数：\n" + "\n".join(lines)
    except Exception as e:
        return f"获取风控配置失败: {e}"


# ============================================================
# 指令路由器
# ============================================================

_COMMAND_PATTERNS = [
    (re.compile(r"^买入\s+(\S+)\s+(\d+)(?:\s+([\d.]+))?$"), "buy"),
    (re.compile(r"^卖出\s+(\S+)\s+(\d+)(?:\s+([\d.]+))?$"), "sell"),
    (re.compile(r"^行情\s+(\S+)$"), "market_data"),
    (re.compile(r"^分析\s+(\S+)$"), "analyze"),
    (re.compile(r"^持仓$"), "positions"),
    (re.compile(r"^订单(?:\s+(all|active|filled|cancelled))?$"), "orders"),
    (re.compile(r"^取消\s+(\S+)$"), "cancel"),
    (re.compile(r"^账户$"), "account"),
    (re.compile(r"^市场分析$"), "market_analysis"),
    (re.compile(r"^板块轮动$"), "sector_rotation"),
    (re.compile(r"^止损扫描$"), "stop_loss"),
    (re.compile(r"^风控$"), "risk_config"),
    (re.compile(r"^帮助$"), "help"),
]


def dispatch_command(text: str) -> str:
    text = text.strip()
    for pattern, cmd_type in _COMMAND_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        try:
            if cmd_type == "buy":
                return cmd_submit_order("buy", m.group(1), int(m.group(2)),
                                        float(m.group(3)) if m.group(3) else None)
            elif cmd_type == "sell":
                return cmd_submit_order("sell", m.group(1), int(m.group(2)),
                                        float(m.group(3)) if m.group(3) else None)
            elif cmd_type == "market_data":
                return cmd_market_data(m.group(1))
            elif cmd_type == "analyze":
                return cmd_analyze(m.group(1))
            elif cmd_type == "positions":
                return cmd_positions()
            elif cmd_type == "orders":
                return cmd_orders(m.group(1) or "all")
            elif cmd_type == "cancel":
                return cmd_cancel_order(m.group(1))
            elif cmd_type == "account":
                return cmd_account()
            elif cmd_type == "market_analysis":
                return cmd_market_analysis()
            elif cmd_type == "sector_rotation":
                return cmd_sector_rotation()
            elif cmd_type == "stop_loss":
                return cmd_scan_stop_loss()
            elif cmd_type == "risk_config":
                return cmd_risk_config()
            elif cmd_type == "help":
                return cmd_help()
        except Exception as e:
            return f"执行失败: {e}"

    return f"无法识别的指令：{text}\n\n输入「帮助」查看支持的指令列表。"


# ============================================================
# 飞书 WebSocket 事件处理
# ============================================================

def _on_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
    """收到飞书消息时的回调"""
    try:
        event = data.event
        msg = event.message
        message_id = msg.message_id
        chat_id = msg.chat_id
        msg_type = msg.message_type

        header = data.header
        event_id = header.event_id if header else ""
        if event_id and _is_duplicate(event_id):
            return

        if msg_type != "text":
            _reply_to_message(message_id, "暂只支持文本指令，请输入「帮助」查看用法。")
            return

        content = json.loads(msg.content or "{}")
        text = content.get("text", "").strip()

        # @机器人 前缀
        if event.message.mentions:
            for mention in event.message.mentions:
                if mention.key:
                    text = text.replace(mention.key, "")
        text = text.strip()

        if not text:
            return

        log.info("收到指令: %s (chat=%s)", text, chat_id)

        result = dispatch_command(text)

        _reply_to_message(message_id, result)

    except Exception as e:
        log.error("处理消息异常: %s", e, exc_info=True)


# ============================================================
# 定时推送：交易日每小时综合市场分析
# ============================================================

def _is_trading_day() -> bool:
    """周一到周五视为交易日"""
    return datetime.now().weekday() < 5


def _in_trading_window() -> bool:
    """
    交易时段（北京时间）：
      A股/港股:  09:00 - 16:30
      美股盘前:  16:00 - 21:30
      美股盘中:  21:30 - 04:00 (次日)
      美股盘后:  04:00 - 06:00 (次日)
    合并覆盖：08:00 - 06:00 (次日)，即只跳过 06:00-07:59
    """
    hour = datetime.now().hour
    return not (6 <= hour <= 7)


def _get_market_session() -> str:
    """根据当前北京时间返回市场时段标签"""
    hour = datetime.now().hour
    if 9 <= hour < 12:
        return "A股/港股 上午盘"
    elif 12 <= hour < 13:
        return "A股午休 | 港股午盘"
    elif 13 <= hour < 16:
        return "A股/港股 下午盘"
    elif 16 <= hour < 21:
        return "美股盘前"
    elif 21 <= hour <= 23:
        return "美股盘中"
    elif 0 <= hour < 4:
        return "美股盘中(夜)"
    elif 4 <= hour < 6:
        return "美股盘后"
    else:
        return "盘前准备"


def _build_full_report() -> str:
    """构建包含 A 股、港股、美股的综合市场报告"""
    import requests as http_req
    sections = []
    now = datetime.now()
    session = _get_market_session()
    sections.append(f"[定时推送 {now.strftime('%Y-%m-%d %H:%M')} | {session}]")

    def _fetch_quote_row(sym: str) -> dict | None:
        """优先走 API 代理，失败后再尝试本地 quote_ctx。"""
        symbol = str(sym or "").strip().upper()
        if not symbol:
            return None
        data = _api_get_json("/internal/longport/quote", {"symbol": symbol}) if USE_API_PROXY else None
        if isinstance(data, dict) and bool(data.get("available")):
            return {
                "symbol": symbol,
                "last": float(data.get("last", 0.0) or 0.0),
                "prev_close": float(data.get("prev_close", 0.0) or 0.0),
                "change_pct": float(data.get("change_pct", 0.0) or 0.0),
                "price_type": str(data.get("price_type", "盘中") or "盘中"),
            }
        if quote_ctx:
            try:
                qs = quote_ctx.quote([symbol])
                if qs:
                    q = qs[0]
                    last, price_type = _get_realtime_price_feishu(q)
                    prev = float(getattr(q, "prev_close", 0.0) or 0.0)
                    chg = ((last - prev) / prev * 100) if prev else 0.0
                    return {
                        "symbol": symbol,
                        "last": float(last),
                        "prev_close": float(prev),
                        "change_pct": float(chg),
                        "price_type": str(price_type),
                    }
            except Exception:
                return None
        return None

    # --- A 股 / 港股 ---
    hk_cn_lines = []
    indices = [
        ("000001.SH", "上证综指"),
        ("399001.SZ", "深证成指"),
        ("HSI.HK", "恒生指数"),
        ("HSTECH.HK", "恒生科技"),
    ]
    for sym, name in indices:
        row = _fetch_quote_row(sym)
        if not row:
            continue
        last = float(row.get("last", 0.0) or 0.0)
        chg = float(row.get("change_pct", 0.0) or 0.0)
        price_type = str(row.get("price_type", "盘中") or "盘中")
        type_tag = f"[{price_type}]" if price_type != "盘中" else ""
        hk_cn_lines.append(f"  {name}: {last:,.2f} ({chg:+.2f}%) {type_tag}")

    if hk_cn_lines:
        sections.append("\n【A股/港股】\n" + "\n".join(hk_cn_lines))
    else:
        sections.append("\n【A股/港股】\n  (数据暂不可用)")

    # --- 美股指数：优先用 LongPort 实时行情（交易时段实时更新），否则用 Stooq 日线 ---
    us_lines = []
    us_realtime = False
    lp_syms = [("SPY.US", "标普500"), ("QQQ.US", "纳指100"), ("DIA.US", "道指")]
    for sym, name in lp_syms:
        row = _fetch_quote_row(sym)
        if not row:
            continue
        last = float(row.get("last", 0.0) or 0.0)
        chg = float(row.get("change_pct", 0.0) or 0.0)
        price_type = str(row.get("price_type", "盘中") or "盘中")
        type_tag = f"[{price_type}]" if price_type != "盘中" else ""
        us_lines.append(f"  {name}: {last:,.2f} ({chg:+.2f}%) {type_tag}")
        us_realtime = True

    if not us_lines:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=7)
        d1 = start_dt.strftime("%Y%m%d")
        d2 = end_dt.strftime("%Y%m%d")
        us_etfs = [("spy.us", "标普500"), ("qqq.us", "纳指100"), ("dia.us", "道指")]
        for sym, name in us_etfs:
            try:
                url = f"https://stooq.com/q/d/l/?s={sym}&d1={d1}&d2={d2}&i=d"
                r = http_req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                lines = r.text.strip().split("\n")
                if len(lines) >= 3:
                    prev_row = lines[-2].split(",")
                    last_row = lines[-1].split(",")
                    last_price = float(last_row[4])
                    prev_price = float(prev_row[4])
                    chg = (last_price - prev_price) / prev_price * 100
                    us_lines.append(f"  {name}: {last_price:,.2f} ({chg:+.2f}%)")
            except Exception:
                pass
            time.sleep(0.2)

    if us_lines:
        src_note = " (实时)" if us_realtime else " (日线)"
        sections.append(f"\n【美股】{src_note}\n" + "\n".join(us_lines))

    # --- 宏观指标 + 情绪 ---
    try:
        from market_analysis import get_comprehensive_analysis
        a = get_comprehensive_analysis()
        ind = a["indicators"]
        fg = ind["fear_greed_index"]
        vix = ind["vix"]
        ty = ind["treasury_10y"]
        dxy = ind["dollar_index"]
        news = ind.get("news_sentiment", {})
        crypto = ind.get("crypto_risk", {})
        crypto_source = crypto.get("source", "unknown")
        cache_age = crypto.get("cache_age_seconds")
        if crypto_source == "cache" and isinstance(cache_age, int):
            crypto_source_text = f"{crypto_source} ({cache_age}s)"
        else:
            crypto_source_text = str(crypto_source)
        sections.append(
            f"\n【宏观环境】(日线/盘后更新)\n"
            f"  市场情绪: {fg['value']}/100 ({fg['level']})\n"
            f"  VIX: {vix['value']}  10Y国债: {ty['value']}%  美元指数: {dxy['value']}\n"
            f"  新闻情绪: {news.get('level', '中性')} ({news.get('score', 0)})\n"
            f"  风险资产温度(BTC/ETH): {crypto.get('level', '中性')} ({crypto.get('avg_change_24h', 0)}%)\n"
            f"  数据源: {crypto_source_text}\n"
            f"  综合评分: {a['score']}/5 | {a['market_environment']}\n"
            f"  策略建议: {a['strategy_recommendation']}"
        )
    except Exception as e:
        sections.append(f"\n【宏观环境】\n  数据获取失败: {e}")

    # --- 美股板块轮动 ---
    try:
        from market_analysis import get_sector_rotation
        s = get_sector_rotation(days=5)
        if "error" not in s and s.get("top_performers"):
            top = ", ".join(f"{x['name']}({x['change_pct']:+.2f}%)" for x in s["top_performers"][:3])
            bot = ", ".join(f"{x['name']}({x['change_pct']:+.2f}%)" for x in s["bottom_performers"][:3])
            sections.append(
                f"\n【美股板块轮动（近5日）】\n"
                f"  强势: {top}\n"
                f"  弱势: {bot}"
            )
    except Exception:
        pass

    return "\n".join(sections)


# ============================================================
# 底部反转信号监控
# ============================================================

WATCH_SYMBOLS = ["RXRX.US"]
_SIGNAL_CHECK_INTERVAL = 1800  # 30 分钟检测一次
_last_signal_cache: dict[str, str] = {}
_REVERSAL_CONDITION_ORDER = [
    "rsi_rebound",
    "macd_bullish_cross_below_zero",
    "bollinger_rebound",
    "hammer_candle",
    "volume_rebound",
    "ma5_cross_ma20",
]


def _load_builtin_reversal_condition_pref() -> tuple[str, set[str]]:
    """读取飞书内置反转线程条件偏好。默认全开+多选。"""
    try:
        from api.notification_preferences import load_notification_preferences

        prefs = load_notification_preferences()
    except Exception:
        prefs = {}
    cfg = prefs.get("feishu_builtin_reversal_monitor") if isinstance(prefs, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}
    mode = str(cfg.get("selection_mode", "multi")).strip().lower()
    if mode not in {"multi", "single"}:
        mode = "multi"
    selected = cfg.get("selected_conditions")
    if not isinstance(selected, list):
        selected = []
    picked = [str(x).strip() for x in selected if str(x).strip() in _REVERSAL_CONDITION_ORDER]
    if not picked:
        picked = list(_REVERSAL_CONDITION_ORDER)
    if mode == "single":
        picked = picked[:1]
    return mode, set(picked)


def _calc_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = diffs[-period:]
    gains = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.0001
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _calc_ema(data: list[float], period: int) -> list[float]:
    if not data:
        return []
    ema = [data[0]]
    k = 2 / (period + 1)
    for i in range(1, len(data)):
        ema.append(data[i] * k + ema[-1] * (1 - k))
    return ema


def _detect_reversal_signals(symbol: str) -> list[str]:
    """检测底部反转信号，返回触发的信号列表"""
    if not quote_ctx:
        return []

    from longport.openapi import Period, AdjustType, TradeSessions
    end = date.today()
    start = end - timedelta(days=60)
    try:
        cs = quote_ctx.history_candlesticks_by_date(
            symbol=symbol,
            period=Period.Day,
            adjust_type=AdjustType.ForwardAdjust,
            start=start,
            end=end,
            trade_sessions=TradeSessions.All,
        )
    except Exception as e:
        log.warning("获取 %s K 线失败: %s", symbol, e)
        return []

    if not cs or len(cs) < 30:
        return []

    closes = [float(c.close) for c in cs]
    highs = [float(c.high) for c in cs]
    lows = [float(c.low) for c in cs]
    opens = [float(c.open) for c in cs]
    volumes = [float(c.volume) for c in cs]

    triggered: list[tuple[str, str]] = []

    # --- 1) RSI 超卖反转：RSI 从 <30 回升到 >30 ---
    rsi_now = _calc_rsi(closes)
    rsi_prev = _calc_rsi(closes[:-1])
    if rsi_prev < 30 and rsi_now >= 30:
        triggered.append(("rsi_rebound", f"RSI 超卖反转 (RSI: {rsi_prev:.1f} -> {rsi_now:.1f})"))
    elif rsi_now < 30:
        triggered.append(("rsi_rebound", f"RSI 超卖区 (RSI={rsi_now:.1f})，关注反转"))

    # --- 2) MACD 金叉（零轴下方）---
    ema12 = _calc_ema(closes, 12)
    ema26 = _calc_ema(closes, 26)
    if len(ema12) >= 2 and len(ema26) >= 2:
        macd_line = [ema12[i] - ema26[i] for i in range(len(ema26))]
        signal_line = _calc_ema(macd_line, 9)
        if len(signal_line) >= 2 and len(macd_line) >= 2:
            prev_diff = macd_line[-2] - signal_line[-2]
            curr_diff = macd_line[-1] - signal_line[-1]
            if prev_diff < 0 and curr_diff >= 0 and macd_line[-1] < 0:
                triggered.append(("macd_bullish_cross_below_zero", f"MACD 零轴下方金叉 (DIF={macd_line[-1]:.3f})"))

    # --- 3) 布林带下轨反弹 ---
    if len(closes) >= 20:
        ma20 = sum(closes[-20:]) / 20
        std20 = (sum((c - ma20) ** 2 for c in closes[-20:]) / 20) ** 0.5
        lower_band = ma20 - 2 * std20
        if lows[-2] <= lower_band and closes[-1] > lower_band:
            triggered.append(("bollinger_rebound", f"布林带下轨反弹 (下轨={lower_band:.2f}, 收盘={closes[-1]:.2f})"))

    # --- 4) 锤子线形态 ---
    o, h, l, c_val = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c_val - o)
    total = h - l if h != l else 0.001
    lower_shadow = min(o, c_val) - l
    upper_shadow = h - max(o, c_val)
    if (lower_shadow > body * 2 and upper_shadow < body * 0.5
            and c_val > o and total > 0 and body / total < 0.35):
        triggered.append(("hammer_candle", f"锤子线形态 (实体={body:.2f}, 下影线={lower_shadow:.2f})"))

    # --- 5) 放量反弹 ---
    if len(volumes) >= 21:
        avg_vol = sum(volumes[-21:-1]) / 20
        if (closes[-1] > closes[-2] and volumes[-1] > avg_vol * 1.5
                and closes[-2] < closes[-3]):
            triggered.append(("volume_rebound", f"放量反弹 (量比={volumes[-1] / avg_vol:.1f}x)"))

    # --- 6) MA5 上穿 MA20 ---
    if len(closes) >= 20:
        ma5_now = sum(closes[-5:]) / 5
        ma20_now = sum(closes[-20:]) / 20
        ma5_prev = sum(closes[-6:-1]) / 5
        ma20_prev = sum(closes[-21:-1]) / 20
        if ma5_prev < ma20_prev and ma5_now >= ma20_now:
            triggered.append(("ma5_cross_ma20", f"均线金叉 MA5 上穿 MA20 (MA5={ma5_now:.2f}, MA20={ma20_now:.2f})"))

    mode, enabled_ids = _load_builtin_reversal_condition_pref()
    picked = [text for cid, text in triggered if cid in enabled_ids]
    if mode == "single" and picked:
        return [picked[0]]
    return picked


def _signal_monitor_loop():
    """后台线程：监控底部反转信号"""
    log.info("信号监控线程启动: %s (每 %d 秒检测)", WATCH_SYMBOLS, _SIGNAL_CHECK_INTERVAL)
    time.sleep(30)  # 启动后等 30 秒再开始

    while True:
        try:
            if not (_is_trading_day() and _in_trading_window()):
                time.sleep(300)
                continue

            for symbol in WATCH_SYMBOLS:
                signals = _detect_reversal_signals(symbol)
                if not signals:
                    log.info("信号监控 %s: 无反转信号", symbol)
                    time.sleep(_SIGNAL_CHECK_INTERVAL)
                    continue

                sig_key = f"{symbol}_{date.today()}_{','.join(signals)}"
                if sig_key == _last_signal_cache.get(symbol):
                    log.info("信号监控 %s: 信号未变化，不重复推送", symbol)
                    time.sleep(_SIGNAL_CHECK_INTERVAL)
                    continue

                _last_signal_cache[symbol] = sig_key

                # 获取当前价格（支持盘前盘后）
                price_str = ""
                try:
                    qs = quote_ctx.quote([symbol])
                    if qs:
                        q = qs[0]
                        last, price_type = _get_realtime_price_feishu(q)
                        prev = float(q.prev_close)
                        chg = ((last - prev) / prev * 100) if prev else 0
                        type_tag = f"[{price_type}]" if price_type != '盘中' else ''
                        price_str = f"  现价: {last:.2f} ({chg:+.2f}%) {type_tag}\n"
                except Exception:
                    pass

                text = (
                    f"[底部反转信号] {symbol}\n"
                    f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                    f"{price_str}"
                    f"  检测到 {len(signals)} 个信号:\n"
                )
                for i, s in enumerate(signals, 1):
                    text += f"    {i}. {s}\n"
                text += "\n  注意: 这是技术面信号，请结合基本面和市场环境综合判断。"

                log.info("信号监控 %s: 检测到 %d 个信号，推送中...", symbol, len(signals))
                ok = _send_to_chat(SCHEDULED_CHAT_ID, text)
                log.info("信号监控推送: %s", "成功" if ok else "失败")

            time.sleep(_SIGNAL_CHECK_INTERVAL)

        except Exception as e:
            log.error("信号监控异常: %s", e, exc_info=True)
            time.sleep(60)


def _scheduler_loop():
    """后台线程：交易日每小时整点推送综合市场报告"""
    log.info("定时推送线程启动 (chat_id=%s)", SCHEDULED_CHAT_ID)
    while True:
        try:
            now = datetime.now()
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            wait_seconds = (next_hour - now).total_seconds()
            log.info("定时推送: 下次执行 %s (等待 %.0f 秒)", next_hour.strftime("%H:%M"), wait_seconds)
            time.sleep(wait_seconds)

            if not _is_trading_day():
                log.info("定时推送: 非交易日，跳过")
                continue
            if not _in_trading_window():
                log.info("定时推送: 非交易时段 (06-08)，跳过")
                continue

            try:
                from api.notification_preferences import should_run_scheduled_market_report

                if not should_run_scheduled_market_report():
                    log.info("定时推送: 通知中心已关闭「定时市场分析报告」，跳过")
                    continue
            except Exception:
                pass

            log.info("定时推送: 开始生成综合市场报告...")
            text = _build_full_report()
            ok = _send_to_chat(SCHEDULED_CHAT_ID, text)
            log.info("定时推送: %s", "发送成功" if ok else "发送失败")

        except Exception as e:
            log.error("定时推送异常: %s", e, exc_info=True)
            time.sleep(60)


# ============================================================
# 启动
# ============================================================

_LOCK_FILE = os.path.join(_dir, ".feishu_command_bot.lock")


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            out = (r.stdout or "").strip().lower()
            if not out:
                return False
            if "no tasks are running" in out or "没有运行的任务" in out:
                return False
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _acquire_single_instance() -> bool:
    """确保机器人单实例运行，避免整点重复推送。"""
    pid = os.getpid()

    def _create_lock_file() -> bool:
        try:
            fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(pid))
            return True
        except FileExistsError:
            return False

    if _create_lock_file():
        return True

    # 锁文件已存在，判断是否为僵尸锁；若是则清理并重试一次。
    old_pid = 0
    try:
        with open(_LOCK_FILE, "r", encoding="utf-8") as f:
            old_pid = int((f.read() or "0").strip() or "0")
    except Exception:
        old_pid = 0

    if _pid_is_running(old_pid):
        log.error("检测到已有 feishu_command_bot 运行中 (pid=%s)，本实例退出。", old_pid)
        return False

    try:
        os.remove(_LOCK_FILE)
    except Exception:
        pass
    if _create_lock_file():
        return True

    log.error("获取单实例锁失败，本实例退出。")
    return False


def _release_single_instance() -> None:
    try:
        if not os.path.exists(_LOCK_FILE):
            return
        with open(_LOCK_FILE, "r", encoding="utf-8") as f:
            owner_pid = int((f.read() or "0").strip() or "0")
        if owner_pid == os.getpid():
            os.remove(_LOCK_FILE)
    except Exception:
        pass


def main():
    if not _acquire_single_instance():
        sys.exit(0)
    atexit.register(_release_single_instance)
    _write_pid_file()
    atexit.register(_remove_pid_file)

    if not APP_ID or not APP_SECRET:
        log.error(
            "feishu_app.app_id / app_secret 未配置！\n"
            "请在 notification_config.json 的 feishu_app 段填写 App ID 和 App Secret。\n"
            "飞书开放平台: https://open.feishu.cn -> 控制台 -> 创建应用 -> 凭证与基础信息"
        )
        sys.exit(1)

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message)
        .build()
    )

    ws_client = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    log.info("=" * 50)
    log.info("飞书指令机器人启动（WebSocket 长连接模式）")
    log.info("无需公网 IP，bot 主动连接飞书服务器")
    if USE_API_PROXY:
        log.info("LongPort: API代理模式 (FEISHU_BOT_USE_API_PROXY=true)")
    else:
        log.info("LongPort: %s", "已连接" if quote_ctx else "未配置")

    if SCHEDULED_CHAT_ID:
        t = threading.Thread(target=_scheduler_loop, daemon=True)
        t.start()
        log.info("定时推送: 已启动（交易日 08:00-次日05:59 每小时整点）")

        try:
            from api.notification_preferences import should_run_feishu_builtin_reversal

            _builtin_rev = should_run_feishu_builtin_reversal()
        except Exception:
            _builtin_rev = False

        if _builtin_rev and (not USE_API_PROXY) and quote_ctx and WATCH_SYMBOLS:
            t2 = threading.Thread(target=_signal_monitor_loop, daemon=True)
            t2.start()
            log.info("信号监控: 已启动内置多条件反转检测 %s", WATCH_SYMBOLS)
        elif WATCH_SYMBOLS:
            log.info(
                "信号监控: 飞书内置反转线程未启用（默认关闭，与信号中心同源请用通知中心「API 底部反转监控」）。"
                "若需机器人内置检测请在通知中心打开相应开关并重启飞书机器人。"
            )
    else:
        log.info("定时推送: 未配置 scheduled_chat_id，跳过")

    log.info("=" * 50)

    ws_client.start()


if __name__ == "__main__":
    main()
