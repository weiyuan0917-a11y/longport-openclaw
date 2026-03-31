"""
LongPort MCP Server v4.1
新增功能：
  - 交易日志系统（8个工具）
  - 智能告警系统（7个工具）
  - 回测系统（3个工具）
  - 风控系统（6个工具）
  -市场分析：4个
  -通知推送：2个

"""
import sys
import os
import io

import mcp.types as types
from typing import Any
import asyncio
import json
import hashlib
import threading
import atexit
import time
import subprocess
from datetime import datetime, date, timedelta

# 加载本地模块
_dir = os.path.dirname(__file__)
sys.path.insert(0, _dir)
from risk_manager import get_manager, load_config, save_config, RiskConfig, append_trade_log
from backtest_engine import BacktestEngine, Bar, coerce_bar_datetime
from strategies import get_strategy, list_strategy_metadata, list_strategy_names
from options_service import (
    build_order_legs,
    estimate_option_fee_for_legs,
    submit_option_order_with_risk,
    fetch_option_expiries,
    fetch_option_chain,
    get_option_positions as svc_get_option_positions,
    get_option_orders as svc_get_option_orders,
    run_option_backtest as svc_run_option_backtest,
)
from ml_common import FEATURE_COLUMNS, build_ml_feature_frame, create_ml_classifier

# ── 新增：交易日志 + 告警系统 ────────────────────────────
from mcp_extensions import (
    get_journal_tools, get_alert_tools, TOOL_DISPATCH,
    get_market_tools, get_notification_tools
)

# ─── LongPort 初始化（懒加载，避免启动即占用连接）──────────────────────
from longport.openapi import Config, QuoteContext, TradeContext, TradeSessions

_ctx_lock = threading.RLock()
_quote_ctx = None
_trade_ctx = None
_PID_FILE = os.path.join(_dir, ".longport_mcp.pid")
_LOCK_FILE = os.path.join(_dir, ".longport_mcp.lock")
_bg_lock = threading.RLock()
_bg_started = False


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
    except PermissionError:
        return True
    except Exception:
        return False


def _acquire_single_instance() -> bool:
    try:
        if os.path.exists(_LOCK_FILE):
            with open(_LOCK_FILE, "r", encoding="utf-8") as f:
                old_pid = int((f.read() or "0").strip() or "0")
            if old_pid > 0 and _pid_is_running(old_pid):
                print(f"[longport-mcp] already running (pid={old_pid}), exit duplicate.", file=sys.stderr)
                return False
    except Exception:
        pass
    try:
        with open(_LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        return False
    return True


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


def _load_longport_credentials() -> tuple[str | None, str | None, str | None]:
    app_key = os.getenv("LONGPORT_APP_KEY")
    app_secret = os.getenv("LONGPORT_APP_SECRET")
    access_token = os.getenv("LONGPORT_ACCESS_TOKEN")
    if app_key and app_secret and access_token:
        return app_key, app_secret, access_token
    try:
        sys.path.insert(0, os.getenv("PYTHONPATH", "D:\\longport-openclaw"))
        from config.live_settings import live_settings

        return (
            live_settings.LONGPORT_APP_KEY,
            live_settings.LONGPORT_APP_SECRET,
            live_settings.LONGPORT_ACCESS_TOKEN,
        )
    except Exception:
        return app_key, app_secret, access_token


def _create_contexts():
    app_key, app_secret, access_token = _load_longport_credentials()
    if not app_key or not app_secret or not access_token:
        raise RuntimeError("LongPort credentials not configured")
    cfg = Config(
        app_key=app_key,
        app_secret=app_secret,
        access_token=access_token,
        enable_overnight=True,
        enable_print_quote_packages=False,
    )
    return QuoteContext(cfg), TradeContext(cfg)


def _close_context(ctx: Any) -> None:
    if ctx is None:
        return
    close_fn = getattr(ctx, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def ensure_contexts():
    global _quote_ctx, _trade_ctx
    with _ctx_lock:
        if _quote_ctx is not None and _trade_ctx is not None:
            return _quote_ctx, _trade_ctx
        _quote_ctx, _trade_ctx = _create_contexts()
        return _quote_ctx, _trade_ctx


def _reset_contexts() -> None:
    global _quote_ctx, _trade_ctx
    with _ctx_lock:
        _close_context(_quote_ctx)
        _close_context(_trade_ctx)
        _quote_ctx = None
        _trade_ctx = None


def _start_background_features_once() -> None:
    """
    Start optional background features lazily.
    This avoids opening LongPort-related resources during discovery (initialize/tools/list).
    """
    global _bg_started
    with _bg_lock:
        if _bg_started:
            return
        # ── 可选：启动告警后台监控 ─────────────────────
        try:
            from alert_manager import get_alert_manager

            alert_mgr = get_alert_manager()
            alert_mgr.start_monitoring(interval=5)
        except Exception:
            pass
        # ── 可选：启动定时任务 ────────────────────────
        try:
            from scheduler import start_all_tasks

            asyncio.create_task(start_all_tasks())
        except Exception:
            pass
        _bg_started = True


atexit.register(_remove_pid_file)


class _LazyContextProxy:
    def __init__(self, which: str):
        self.which = which

    def _resolve(self):
        q, t = ensure_contexts()
        return q if self.which == "quote" else t

    def __getattr__(self, item: str):
        return getattr(self._resolve(), item)


quote_ctx = _LazyContextProxy("quote")
trade_ctx = _LazyContextProxy("trade")

# ============================================================
# OpenClaw MCP 分级授权（L1/L2/L3）
# ============================================================

_LEVEL_RANK = {"L1": 1, "L2": 2, "L3": 3}


def _normalize_level(raw: str | None, default: str = "L2") -> str:
    x = str(raw or "").strip().upper()
    return x if x in _LEVEL_RANK else default


def _env_bool(name: str, default: bool = False) -> bool:
    val = str(os.getenv(name, "")).strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "on"}


MCP_MAX_TOOL_LEVEL = _normalize_level(os.getenv("OPENCLAW_MCP_MAX_LEVEL", "L2"), default="L2")
MCP_ALLOW_L3_TOOLS = _env_bool("OPENCLAW_MCP_ALLOW_L3", default=False)
MCP_L3_CONFIRMATION_TOKEN = str(os.getenv("OPENCLAW_MCP_L3_CONFIRMATION_TOKEN", "")).strip()
MCP_DEBOUNCE_ENABLED = _env_bool("OPENCLAW_MCP_DEBOUNCE_ENABLED", default=True)
MCP_DEBOUNCE_SECONDS = max(0.0, min(float(os.getenv("OPENCLAW_MCP_DEBOUNCE_SECONDS", "1.5")), 10.0))
MCP_DEBOUNCE_CACHE_TTL_SECONDS = max(
    MCP_DEBOUNCE_SECONDS,
    min(float(os.getenv("OPENCLAW_MCP_DEBOUNCE_CACHE_TTL_SECONDS", "8.0")), 60.0),
)

DEBOUNCE_TOOL_ALLOWLIST = {
    "get_account_info",
    "get_market_data",
    "analyze_stock",
    "get_historical_bars",
    "get_financials",
    "get_option_chain",
    "get_option_expiries",
    "get_intraday",
    "get_watchlist",
    "get_positions",
    "get_orders",
    "get_option_positions",
    "get_option_orders",
}

_TOOL_DEBOUNCE_CACHE: dict[str, dict[str, Any]] = {}
_TOOL_DEBOUNCE_LAST_TS: dict[str, float] = {}
_TOOL_DEBOUNCE_LOCK = threading.RLock()


TOOL_LEVELS: dict[str, str] = {
    # L1: 只读查询
    "get_account_info": "L1",
    "get_market_data": "L1",
    "analyze_stock": "L1",
    "get_historical_bars": "L1",
    "get_financials": "L1",
    "get_option_chain": "L1",
    "get_option_expiries": "L1",
    "estimate_option_order_fee": "L1",
    "get_option_orders": "L1",
    "get_option_positions": "L1",
    "get_intraday": "L1",
    "get_watchlist": "L1",
    "get_positions": "L1",
    "get_orders": "L1",
    "check_risk": "L1",
    "get_risk_config": "L1",
    "list_strategies": "L1",
    "get_trade_history": "L1",
    "analyze_decision_quality": "L1",
    "get_trade_statistics": "L1",
    "find_similar_trades": "L1",
    "list_alerts": "L1",
    "get_alert_statistics": "L1",
    "get_market_sentiment": "L1",
    "get_macro_indicators": "L1",
    "get_market_analysis": "L1",
    "get_sector_rotation": "L1",
    # L2: 研究/运营类（可写但非实盘交易）
    "run_ml_strategy": "L2",
    "build_factor_model": "L2",
    "optimize_portfolio": "L2",
    "run_backtest": "L2",
    "run_option_backtest": "L2",
    "compare_strategies": "L2",
    "set_price_alert": "L2",
    "set_volume_alert": "L2",
    "delete_alert": "L2",
    "check_triggered_alerts": "L2",
    "start_alert_monitor": "L2",
    "save_trade_note": "L2",
    "update_trade_exit": "L2",
    "add_trade_review": "L2",
    "generate_review": "L2",
    "send_notification": "L2",
    "test_notification": "L2",
    "scan_stop_loss": "L2",
    # L3: 实盘敏感
    "submit_order": "L3",
    "submit_option_order_single_leg": "L3",
    "submit_option_order_multi_leg": "L3",
    "cancel_order": "L3",
    "set_risk_config": "L3",
    "emergency_flatten": "L3",
}


def _tool_level(name: str) -> str:
    return TOOL_LEVELS.get(name, "L2")


def _error_response(
    error_code: str,
    message: str,
    hint: str = "",
    retryable: bool = False,
    extra: dict[str, Any] | None = None,
) -> list[types.TextContent]:
    payload: dict[str, Any] = {
        "error_code": error_code,
        "message": message,
        "hint": hint,
        "retryable": retryable,
    }
    if extra:
        payload.update(extra)
    return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _authorize_tool_call(name: str, arguments: dict[str, Any]) -> list[types.TextContent] | None:
    level = _tool_level(name)
    required_rank = _LEVEL_RANK.get(level, 2)
    current_rank = _LEVEL_RANK.get(MCP_MAX_TOOL_LEVEL, 2)

    if current_rank < required_rank:
        return _error_response(
            error_code="permission_denied",
            message=f"工具 {name} 需要 {level} 权限，当前仅允许到 {MCP_MAX_TOOL_LEVEL}",
            hint="提升 OPENCLAW_MCP_MAX_LEVEL（L1/L2/L3）后重试。",
            retryable=False,
            extra={"tool": name, "required_level": level, "current_level": MCP_MAX_TOOL_LEVEL},
        )

    if level == "L3":
        if not MCP_ALLOW_L3_TOOLS:
            return _error_response(
                error_code="tool_disabled",
                message=f"实盘敏感工具 {name} 当前已禁用",
                hint="将 OPENCLAW_MCP_ALLOW_L3=true 后重试。",
                retryable=False,
                extra={"tool": name, "required_level": "L3"},
            )
        if MCP_L3_CONFIRMATION_TOKEN:
            token = str(arguments.get("confirmation_token", "")).strip()
            if token != MCP_L3_CONFIRMATION_TOKEN:
                return _error_response(
                    error_code="confirmation_required",
                    message=f"调用 {name} 需要有效 confirmation_token",
                    hint="在参数中添加 confirmation_token（值为 OPENCLAW_MCP_L3_CONFIRMATION_TOKEN）。",
                    retryable=False,
                    extra={"tool": name, "required_level": "L3"},
                )
    return None


def _debounce_cache_key(name: str, arguments: dict[str, Any]) -> str:
    try:
        payload = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        payload = str(arguments or {})
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{name}:{digest}"


def _is_error_response(contents: list[types.TextContent | types.ImageContent | types.EmbeddedResource]) -> bool:
    if not contents:
        return False
    first = contents[0]
    txt = getattr(first, "text", None)
    if not isinstance(txt, str):
        return False
    try:
        payload = json.loads(txt)
        return isinstance(payload, dict) and bool(payload.get("error_code"))
    except Exception:
        return False


def _get_debounced_response(
    name: str,
    arguments: dict[str, Any],
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource] | None:
    if not MCP_DEBOUNCE_ENABLED or name not in DEBOUNCE_TOOL_ALLOWLIST:
        return None
    now = time.time()
    key = _debounce_cache_key(name, arguments)
    with _TOOL_DEBOUNCE_LOCK:
        item = _TOOL_DEBOUNCE_CACHE.get(key)
        if item:
            ts = float(item.get("ts", 0.0))
            if now - ts <= MCP_DEBOUNCE_SECONDS:
                return item.get("response")
            if now - ts > MCP_DEBOUNCE_CACHE_TTL_SECONDS:
                _TOOL_DEBOUNCE_CACHE.pop(key, None)
    return None


def _put_debounced_response(
    name: str,
    arguments: dict[str, Any],
    response: list[types.TextContent | types.ImageContent | types.EmbeddedResource],
) -> None:
    if not MCP_DEBOUNCE_ENABLED or name not in DEBOUNCE_TOOL_ALLOWLIST:
        return
    if _is_error_response(response):
        return
    now = time.time()
    key = _debounce_cache_key(name, arguments)
    with _TOOL_DEBOUNCE_LOCK:
        last = _TOOL_DEBOUNCE_LAST_TS.get(name, 0.0)
        # 仅在高频调用场景缓存，避免长期污染。
        if now - last <= MCP_DEBOUNCE_CACHE_TTL_SECONDS:
            _TOOL_DEBOUNCE_CACHE[key] = {"response": response, "ts": now}
        _TOOL_DEBOUNCE_LAST_TS[name] = now


# ============================================================
# 工具列表
# ============================================================

async def handle_list_tools() -> list[types.Tool]:
    strategy_names = list_strategy_names()
    return [
        # ── 行情 / 账户 ───────────────────────────────────
        types.Tool(
            name="get_account_info",
            description="获取账户信息",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_market_data",
            description="获取实时行情（含盘前盘后夜盘）",
            inputSchema={"type": "object",
                "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
        ),
        types.Tool(
            name="analyze_stock",
            description="技术分析（MA/RSI + 建议）",
            inputSchema={"type": "object",
                "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
        ),
        types.Tool(
            name="get_historical_bars",
            description="获取历史K线数据（用于回测/分析）",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 AAPL.US"},
                    "period": {
                        "type": "string",
                        "enum": ["1m", "5m", "15m", "30m", "60m", "1d", "1w", "1mo"],
                        "description": "K线周期，默认 1d",
                    },
                    "days": {"type": "integer", "description": "回看天数，默认 180"},
                    "limit": {"type": "integer", "description": "返回最近N根K线，默认200，最大2000"},
                    "adjust_type": {
                        "type": "string",
                        "enum": ["forward", "none"],
                        "description": "复权类型，默认 forward",
                    },
                },
                "required": ["symbol"],
            },
        ),
        types.Tool(
            name="get_financials",
            description="获取估值/财务相关指标（PE/PB/股息率/每股收益等）",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 AAPL.US"},
                },
                "required": ["symbol"],
            },
        ),
        types.Tool(
            name="get_option_chain",
            description="获取期权链（支持指定到期日）",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "标的代码，如 AAPL.US"},
                    "expiry_date": {
                        "type": "string",
                        "description": "到期日 YYYY-MM-DD，不填默认最近一期",
                    },
                    "min_strike": {"type": "number", "description": "最小执行价过滤"},
                    "max_strike": {"type": "number", "description": "最大执行价过滤"},
                    "standard_only": {"type": "boolean", "description": "仅返回标准合约，默认 false"},
                    "limit": {"type": "integer", "description": "返回数量，默认100，最大500"},
                    "offset": {"type": "integer", "description": "分页偏移，默认0"},
                },
                "required": ["symbol"],
            },
        ),
        types.Tool(
            name="get_option_expiries",
            description="获取期权到期日列表",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "标的代码，如 AAPL.US"},
                },
                "required": ["symbol"],
            },
        ),
        types.Tool(
            name="get_intraday",
            description="获取当日分时数据",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码，如 AAPL.US"},
                },
                "required": ["symbol"],
            },
        ),
        types.Tool(
            name="get_watchlist",
            description="获取自选股列表及最新行情",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="run_ml_strategy",
            description="运行机器学习策略，输出信号与评估指标",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "股票代码列表，如 [\"AAPL.US\", \"TSLA.US\"]",
                    },
                    "lookback_days": {"type": "integer", "description": "训练回看天数，默认 365"},
                    "horizon_days": {"type": "integer", "description": "预测周期（未来N天收益方向），默认 5"},
                    "model_type": {
                        "type": "string",
                        "enum": ["logreg", "random_forest", "gbdt"],
                        "description": "模型类型，默认 logreg",
                    },
                    "threshold": {"type": "number", "description": "买入阈值（0-1），默认 0.55"},
                    "transaction_cost_bps": {"type": "number", "description": "标签净收益扣减成本（bps），默认 16"},
                    "min_samples": {"type": "integer", "description": "最小样本数，默认 120"},
                    "cache_minutes": {"type": "integer", "description": "缓存分钟数，默认15，0为不缓存"},
                    "rebalance": {
                        "type": "string",
                        "enum": ["none", "daily", "weekly", "monthly"],
                        "description": "再平衡频率，默认 weekly",
                    },
                    "force_refresh": {"type": "boolean", "description": "是否跳过缓存强制刷新，默认 false"},
                },
                "required": ["symbols"],
            },
        ),
        types.Tool(
            name="build_factor_model",
            description="构建因子模型，输出因子暴露与IC统计",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "股票代码列表",
                    },
                    "factors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "因子列表，默认 [momentum, volatility, ma_gap, rsi]",
                    },
                    "lookback_days": {"type": "integer", "description": "回看天数，默认 365"},
                    "horizon_days": {"type": "integer", "description": "未来收益窗口，默认 5"},
                    "top_n": {"type": "integer", "description": "输出前N名股票，默认 10"},
                    "cache_minutes": {"type": "integer", "description": "缓存分钟数，默认15，0为不缓存"},
                    "rebalance": {
                        "type": "string",
                        "enum": ["none", "daily", "weekly", "monthly"],
                        "description": "再平衡频率，默认 weekly",
                    },
                    "force_refresh": {"type": "boolean", "description": "是否跳过缓存强制刷新，默认 false"},
                },
                "required": ["symbols"],
            },
        ),
        types.Tool(
            name="optimize_portfolio",
            description="组合优化（均值方差/风险平价）",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "股票代码列表",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["mean_variance", "risk_parity"],
                        "description": "优化方法，默认 mean_variance",
                    },
                    "lookback_days": {"type": "integer", "description": "估计窗口天数，默认 252"},
                    "risk_aversion": {"type": "number", "description": "风险厌恶系数，仅均值方差有效，默认 3.0"},
                    "min_weight": {"type": "number", "description": "单标的最小权重，默认 0"},
                    "max_weight": {"type": "number", "description": "单标的最大权重，默认 0.4"},
                    "cache_minutes": {"type": "integer", "description": "缓存分钟数，默认15，0为不缓存"},
                    "rebalance": {
                        "type": "string",
                        "enum": ["none", "daily", "weekly", "monthly"],
                        "description": "再平衡频率，默认 monthly",
                    },
                    "force_refresh": {"type": "boolean", "description": "是否跳过缓存强制刷新，默认 false"},
                },
                "required": ["symbols"],
            },
        ),
        # ── 交易 ──────────────────────────────────────────
        types.Tool(
            name="estimate_option_order_fee",
            description="估算期权单腿/多腿订单费用",
            inputSchema={
                "type": "object",
                "properties": {
                    "legs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "symbol": {"type": "string"},
                                "side": {"type": "string", "enum": ["buy", "sell"]},
                                "contracts": {"type": "integer"},
                                "price": {"type": "number"},
                            },
                            "required": ["symbol", "side", "contracts"],
                        },
                    },
                },
                "required": ["legs"],
            },
        ),
        types.Tool(
            name="submit_option_order_single_leg",
            description="提交期权单腿订单（L3）",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "contracts": {"type": "integer"},
                    "price": {"type": "number"},
                    "max_loss_threshold": {"type": "number"},
                    "max_capital_usage": {"type": "number"},
                    "confirmation_token": {"type": "string", "description": "L3工具确认令牌（可选）"},
                },
                "required": ["symbol", "side", "contracts"],
            },
        ),
        types.Tool(
            name="submit_option_order_multi_leg",
            description="提交期权多腿组合订单（L3）",
            inputSchema={
                "type": "object",
                "properties": {
                    "legs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "symbol": {"type": "string"},
                                "side": {"type": "string", "enum": ["buy", "sell"]},
                                "contracts": {"type": "integer"},
                                "price": {"type": "number"},
                            },
                            "required": ["symbol", "side", "contracts"],
                        },
                    },
                    "max_loss_threshold": {"type": "number"},
                    "max_capital_usage": {"type": "number"},
                    "confirmation_token": {"type": "string", "description": "L3工具确认令牌（可选）"},
                },
                "required": ["legs"],
            },
        ),
        types.Tool(
            name="get_option_positions",
            description="获取期权持仓列表",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_option_orders",
            description="获取期权订单列表",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["all", "active", "filled", "cancelled"],
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="submit_order",
            description="提交订单（自动风控）",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol":   {"type": "string"},
                    "action":   {"type": "string", "enum": ["buy", "sell"]},
                    "quantity": {"type": "integer"},
                    "price":    {"type": "number"},
                    "confirmation_token": {"type": "string", "description": "L3工具确认令牌（可选）"},
                },
                "required": ["symbol", "action", "quantity"],
            },
        ),
        types.Tool(
            name="get_positions",
            description="获取所有持仓",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_orders",
            description="获取今日订单",
            inputSchema={"type": "object",
                "properties": {"status": {"type": "string",
                    "enum": ["all","active","filled","cancelled"]}}, "required": []},
        ),
        types.Tool(
            name="cancel_order",
            description="取消订单",
            inputSchema={"type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "confirmation_token": {"type": "string", "description": "L3工具确认令牌（可选）"},
                }, "required": ["order_id"]},
        ),
        # ── 风控 ──────────────────────────────────────────
        types.Tool(
            name="check_risk",
            description="下单前风控预检查",
            inputSchema={"type": "object",
                "properties": {
                    "symbol":   {"type": "string"},
                    "action":   {"type": "string", "enum": ["buy","sell"]},
                    "quantity": {"type": "integer"},
                    "price":    {"type": "number"},
                }, "required": ["symbol","action","quantity","price"]},
        ),
        types.Tool(
            name="get_risk_config",
            description="查看风控参数",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="set_risk_config",
            description="修改风控参数",
            inputSchema={"type": "object",
                "properties": {
                    "max_order_amount":   {"type": "number"},
                    "max_daily_loss_pct": {"type": "number"},
                    "stop_loss_pct":      {"type": "number"},
                    "max_position_pct":   {"type": "number"},
                    "enabled":            {"type": "boolean"},
                    "confirmation_token": {"type": "string", "description": "L3工具确认令牌（可选）"},
                }, "required": []},
        ),
        types.Tool(
            name="scan_stop_loss",
            description="扫描持仓止损状态",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="emergency_flatten",
            description="紧急平仓",
            inputSchema={"type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "不填则清仓所有"},
                    "confirmation_token": {"type": "string", "description": "L3工具确认令牌（可选）"},
                }, "required": []},
        ),
        # ── 回测 ──────────────────────────────────────────
        types.Tool(
            name="list_strategies",
            description="列出所有可用回测策略及参数说明",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="run_backtest",
            description=(
                "对指定股票运行单策略回测，返回收益率、最大回撤、夏普比率、胜率等完整报告"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，如 AAPL.US",
                    },
                    "strategy": {
                        "type": "string",
                        "enum": strategy_names,
                        "description": "策略名称",
                    },
                    "days": {
                        "type": "integer",
                        "description": "回测天数，默认 180（约半年）",
                    },
                    "initial_capital": {
                        "type": "number",
                        "description": "初始资金，默认 100000",
                    },
                    "params": {
                        "type": "object",
                        "description": "策略参数，不填使用默认值。如 {\"fast\": 5, \"slow\": 20}",
                    },
                },
                "required": ["symbol", "strategy"],
            },
        ),
        types.Tool(
            name="compare_strategies",
            description="对同一股票同一时段运行所有（或指定）策略，横向对比哪个策略更好",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                    "days":   {"type": "integer", "description": "回测天数，默认 180"},
                    "initial_capital": {"type": "number", "description": "初始资金，默认 100000"},
                    "strategies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要对比的策略列表，不填则对比全部内置策略",
                    },
                },
                "required": ["symbol"],
            },
        ),
        types.Tool(
            name="run_option_backtest",
            description="运行期权策略模板回测（Bull/Bear/Straddle/Strangle）",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "template": {
                        "type": "string",
                        "enum": ["bull_call_spread", "bear_put_spread", "straddle", "strangle"],
                    },
                    "days": {"type": "integer"},
                    "holding_days": {"type": "integer"},
                    "contracts": {"type": "integer"},
                    "width_pct": {"type": "number"},
                },
                "required": ["symbol", "template"],
            },
        ),
        
        # ── 交易日志（新增 8 个工具）─────────────────────
        *get_journal_tools(),
        
        # ── 智能告警（新增 7 个工具）─────────────────────
        *get_alert_tools(),

        *get_market_tools(),  # 新增

        *get_notification_tools(),

    ]


# ============================================================
# 工具分发
# ============================================================

async def handle_call_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:

    arguments = dict(arguments or {})
    try:
        auth_error = _authorize_tool_call(name, arguments)
        if auth_error is not None:
            return auth_error
        # 仅用于授权校验，不下发给具体工具逻辑
        arguments.pop("confirmation_token", None)
        debounced = _get_debounced_response(name, arguments)
        if debounced is not None:
            return debounced

        dispatch = {
            "get_account_info":   get_account_info,
            "get_market_data":    lambda: get_market_data(arguments.get("symbol")),
            "analyze_stock":      lambda: analyze_stock(arguments.get("symbol")),
            "get_historical_bars": lambda: get_historical_bars(arguments),
            "get_financials":      lambda: get_financials(arguments.get("symbol")),
            "get_option_chain":    lambda: get_option_chain(arguments),
            "get_option_expiries": lambda: get_option_expiries(arguments.get("symbol")),
            "get_intraday":        lambda: get_intraday(arguments.get("symbol")),
            "get_watchlist":       get_watchlist,
            "run_ml_strategy":     lambda: run_ml_strategy(arguments),
            "build_factor_model":  lambda: build_factor_model(arguments),
            "optimize_portfolio":  lambda: optimize_portfolio(arguments),
            "estimate_option_order_fee": lambda: estimate_option_order_fee(arguments),
            "submit_option_order_single_leg": lambda: submit_option_order_single_leg(arguments),
            "submit_option_order_multi_leg": lambda: submit_option_order_multi_leg(arguments),
            "get_option_positions": get_option_positions,
            "get_option_orders": lambda: get_option_orders(arguments.get("status", "all")),
            "submit_order":       lambda: submit_order(arguments),
            "get_positions":      get_positions,
            "get_orders":         lambda: get_orders(arguments.get("status","all")),
            "cancel_order":       lambda: cancel_order(arguments.get("order_id")),
            "check_risk":         lambda: check_risk(arguments),
            "get_risk_config":    get_risk_config,
            "set_risk_config":    lambda: set_risk_config(arguments),
            "scan_stop_loss":     scan_stop_loss,
            "emergency_flatten":  lambda: emergency_flatten(arguments.get("symbol")),
            # 回测
            "list_strategies":    list_strategies,
            "run_backtest":       lambda: run_backtest(arguments),
            "compare_strategies": lambda: compare_strategies(arguments),
            "run_option_backtest": lambda: run_option_backtest(arguments),
            
            # ── 新增：交易日志 + 告警（15 个工具）──────────
            **TOOL_DISPATCH,
        }
        fn = dispatch.get(name)
        if fn is None:
            return _error_response(
                error_code="tool_not_found",
                message=f"Unknown tool: {name}",
                hint="请先调用 list_tools 确认可用工具名。",
                retryable=False,
                extra={"tool": name},
            )
        
        # 新工具（交易日志+告警）已经是 async 函数，直接调用
        if name in TOOL_DISPATCH:
            result = await fn(arguments)
        else:
            result = await fn()  # 原有工具

        _put_debounced_response(name, arguments, result)
        return result
            
    except Exception as e:
        err_text = str(e)
        if "connections limitation is hit" in err_text.lower():
            _reset_contexts()
            return _error_response(
                error_code="upstream_connection_limit",
                message="LongPort 连接数达到上限（官方限制）。",
                hint="请减少并发服务实例（API/MCP/机器人）或等待旧连接释放后重试。",
                retryable=True,
                extra={"tool": name},
            )
        return _error_response(
            error_code="internal_error",
            message=f"工具调用失败: {str(e)}",
            hint="可稍后重试；若持续失败请检查参数与上游服务状态。",
            retryable=True,
            extra={"tool": name},
        )


# ============================================================
# 回测工具实现
# ============================================================

def _fetch_bars(symbol: str, days: int) -> list[Bar]:
    """从 LongPort 获取历史K线，转换为 Bar 列表"""
    from longport.openapi import Period, AdjustType
    end_date   = date.today()
    start_date = end_date - timedelta(days=days)
    candles = quote_ctx.history_candlesticks_by_date(
        symbol=symbol,
        period=Period.Day,
        adjust_type=AdjustType.ForwardAdjust,  # 前复权
        start=start_date,
        end=end_date,
        trade_sessions=TradeSessions.All,
    )
    return [
        Bar(
            date=coerce_bar_datetime(c.timestamp),
            open=float(c.open), high=float(c.high),
            low=float(c.low),   close=float(c.close),
            volume=float(c.volume),
        )
        for c in candles
    ]


async def list_strategies() -> list[types.TextContent]:
    """列出所有策略"""
    items = list_strategy_metadata()
    result = {
        "可用策略": items,
        "使用示例": (
            "帮我对 AAPL.US 过去半年跑 MACD 策略回测"
            " / 对比 TSLA.US 所有策略，看哪个最好"
        ),
    }
    return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def run_backtest(args: dict) -> list[types.TextContent]:
    """运行单策略回测"""
    try:
        symbol   = args["symbol"]
        strategy_name = args["strategy"]
        days     = args.get("days", 180)
        capital  = args.get("initial_capital", 100_000)
        params   = args.get("params", {})

        # 获取历史数据
        bars = _fetch_bars(symbol, days)
        if not bars:
            return [types.TextContent(type="text", text=f"无法获取 {symbol} 的历史数据")]

        # 创建策略
        strategy_fn = get_strategy(strategy_name, params)

        # 运行回测
        engine = BacktestEngine(
            bars=bars,
            symbol=symbol,
            strategy_name=strategy_fn.__name__,
            strategy_fn=strategy_fn,
            initial_capital=capital,
        )
        result = engine.run()

        # 返回摘要
        return [types.TextContent(
            type="text",
            text=json.dumps(result.to_summary(), indent=2, ensure_ascii=False)
        )]

    except Exception as e:
        return [types.TextContent(type="text", text=f"回测失败: {str(e)}")]


async def compare_strategies(args: dict) -> list[types.TextContent]:
    """对比多策略"""
    try:
        symbol     = args["symbol"]
        days       = args.get("days", 180)
        capital    = args.get("initial_capital", 100_000)
        strategies = args.get(
            "strategies",
            list_strategy_names(),
        )

        bars = _fetch_bars(symbol, days)
        if not bars:
            return [types.TextContent(type="text", text=f"无法获取 {symbol} 的历史数据")]

        results = []
        for sname in strategies:
            try:
                sfn = get_strategy(sname, None)
                engine = BacktestEngine(
                    bars=bars, symbol=symbol, strategy_name=sfn.__name__,
                    strategy_fn=sfn, initial_capital=capital,
                )
                r = engine.run()
                results.append({
                    "策略":       r.strategy_name,
                    "总收益率":   f"{r.total_return_pct:+.2f}%",
                    "年化收益":   f"{r.annual_return_pct:+.2f}%",
                    "最大回撤":   f"-{r.max_drawdown_pct:.2f}%",
                    "夏普比率":   f"{r.sharpe_ratio:.2f}",
                    "胜率":       f"{r.win_rate_pct:.1f}%",
                    "盈亏比":     f"{r.profit_factor:.2f}",
                    "交易次数":   r.total_trades,
                    "综合评级":   r._rating(),
                })
            except Exception as e:
                results.append({"策略": sname, "错误": str(e)})

        # 按总收益率排序
        results.sort(key=lambda x: float(x.get("总收益率", "0%").replace("%","").replace("+","")), reverse=True)

        output = {
            "股票代码":   symbol,
            "回测区间":   f"近 {days} 天",
            "初始资金":   f"{capital:,.0f}",
            "策略对比排名": results,
            "最优策略":   results[0]["策略"] if results else "N/A",
            "建议": (
                f"在 {symbol} 近 {days} 天的行情中，{results[0]['策略']} 表现最优，"
                f"总收益 {results[0]['总收益率']}，夏普 {results[0]['夏普比率']}。"
                "建议以此策略为主，结合当前市场环境决策。"
            ) if results else "",
        }

        return [types.TextContent(type="text", text=json.dumps(output, indent=2, ensure_ascii=False))]

    except Exception as e:
        return [types.TextContent(type="text", text=f"策略对比失败: {str(e)}")]


async def run_option_backtest(args: dict) -> list[types.TextContent]:
    try:
        result = svc_run_option_backtest(
            fetch_bars_fn=_fetch_bars,
            symbol=str(args["symbol"]),
            template=str(args["template"]),
            days=int(args.get("days", 180)),
            holding_days=int(args.get("holding_days", 20)),
            contracts=int(args.get("contracts", 1)),
            width_pct=float(args.get("width_pct", 0.05)),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"期权回测失败: {str(e)}")]


# ============================================================
# 机器学习 / 因子 / 组合优化工具
# ============================================================

_MCP_TOOL_CACHE: dict[str, dict[str, Any]] = {}


def _cache_key(tool_name: str, args: dict) -> str:
    cache_args = {k: v for k, v in args.items() if k not in {"cache_minutes", "force_refresh"}}
    payload = json.dumps(cache_args, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{tool_name}:{digest}"


def _cache_get(tool_name: str, args: dict) -> dict | None:
    key = _cache_key(tool_name, args)
    item = _MCP_TOOL_CACHE.get(key)
    if not item:
        return None
    expires_at = item.get("expires_at")
    if not isinstance(expires_at, datetime) or datetime.now() >= expires_at:
        _MCP_TOOL_CACHE.pop(key, None)
        return None
    try:
        # 深拷贝，避免后续修改污染缓存
        return json.loads(json.dumps(item.get("data"), ensure_ascii=False))
    except Exception:
        return item.get("data")


def _cache_set(tool_name: str, args: dict, data: dict, cache_minutes: int) -> None:
    if cache_minutes <= 0:
        return
    key = _cache_key(tool_name, args)
    _MCP_TOOL_CACHE[key] = {
        "data": data,
        "expires_at": datetime.now() + timedelta(minutes=cache_minutes),
        "created_at": datetime.now().isoformat(),
    }


def _next_rebalance_time(rebalance: str) -> str | None:
    now = datetime.now()
    mode = str(rebalance or "none").lower()
    if mode == "daily":
        return (now + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0).isoformat()
    if mode == "weekly":
        days = 7 - now.weekday()
        if days <= 0:
            days += 7
        return (now + timedelta(days=days)).replace(hour=9, minute=30, second=0, microsecond=0).isoformat()
    if mode == "monthly":
        year = now.year + (1 if now.month == 12 else 0)
        month = 1 if now.month == 12 else now.month + 1
        return datetime(year, month, 1, 9, 30, 0).isoformat()
    return None


def _normalize_symbol_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        vals = [str(x).strip().upper() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        vals = [x.strip().upper() for x in raw.split(",") if x.strip()]
    else:
        vals = []
    # 去重保序
    out: list[str] = []
    seen = set()
    for s in vals:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


def _safe_corr(a, b) -> float | None:
    try:
        import numpy as np
        if len(a) < 3 or len(b) < 3:
            return None
        x = np.asarray(a, dtype=float)
        y = np.asarray(b, dtype=float)
        if np.nanstd(x) == 0 or np.nanstd(y) == 0:
            return None
        return float(np.corrcoef(x, y)[0, 1])
    except Exception:
        return None


def _build_feature_frame(symbol: str, days: int, horizon_days: int, transaction_cost_bps: float = 16.0):
    bars = _fetch_bars(symbol, days)
    return build_ml_feature_frame(
        bars,
        horizon_days=horizon_days,
        transaction_cost_bps=transaction_cost_bps,
        symbol=symbol,
    )


def _project_weights(weights, min_w: float, max_w: float):
    import numpy as np
    w = np.asarray(weights, dtype=float)
    n = len(w)
    if n == 0:
        return w
    w = np.nan_to_num(w, nan=0.0, posinf=0.0, neginf=0.0)
    if w.sum() <= 0:
        w = np.ones(n, dtype=float) / n
    else:
        w = w / w.sum()
    # 简单迭代投影到 box + sum=1
    for _ in range(20):
        w = np.clip(w, min_w, max_w)
        s = float(w.sum())
        if s == 0:
            w = np.ones(n, dtype=float) / n
            continue
        w = w / s
    return w


async def run_ml_strategy(args: dict) -> list[types.TextContent]:
    """运行机器学习策略"""
    try:
        import pandas as pd
        try:
            from sklearn.metrics import accuracy_score, roc_auc_score
        except Exception:
            return [types.TextContent(
                type="text",
                text="缺少依赖 scikit-learn，请先安装后再运行 run_ml_strategy。",
            )]

        symbols = _normalize_symbol_list(args.get("symbols"))
        if not symbols:
            return [types.TextContent(type="text", text="symbols 不能为空")]

        lookback_days = int(args.get("lookback_days", 365))
        horizon_days = int(args.get("horizon_days", 5))
        model_type = str(args.get("model_type", "logreg"))
        threshold = float(args.get("threshold", 0.55))
        transaction_cost_bps = float(args.get("transaction_cost_bps", 16.0))
        min_samples = int(args.get("min_samples", 120))
        cache_minutes = max(0, min(int(args.get("cache_minutes", 15)), 24 * 60))
        rebalance = str(args.get("rebalance", "weekly")).lower()
        force_refresh = bool(args.get("force_refresh", False))
        if rebalance not in {"none", "daily", "weekly", "monthly"}:
            rebalance = "weekly"

        if cache_minutes > 0 and not force_refresh:
            cached = _cache_get("run_ml_strategy", args)
            if cached is not None:
                cached["cache"] = {
                    "hit": True,
                    "cache_minutes": cache_minutes,
                    "rebalance": rebalance,
                    "next_rebalance_at": _next_rebalance_time(rebalance),
                }
                return [types.TextContent(type="text", text=json.dumps(cached, indent=2, ensure_ascii=False))]

        lookback_days = max(120, min(lookback_days, 3650))
        horizon_days = max(1, min(horizon_days, 30))
        threshold = max(0.5, min(threshold, 0.9))
        transaction_cost_bps = max(0.0, min(transaction_cost_bps, 500.0))
        min_samples = max(60, min(min_samples, 5000))

        frames = []
        latest_rows = []
        for s in symbols:
            df = _build_feature_frame(s, lookback_days, horizon_days, transaction_cost_bps=transaction_cost_bps)
            if df is None or len(df) < 50:
                continue
            frames.append(df)
            latest_rows.append(df.iloc[-1].copy())

        if not frames:
            return [types.TextContent(type="text", text="可用样本不足，无法训练模型")]

        data = pd.concat(frames, ignore_index=True)
        if len(data) < min_samples:
            return [types.TextContent(type="text", text=f"样本不足：当前 {len(data)}，至少需要 {min_samples}")]

        feature_cols = FEATURE_COLUMNS
        X = data[feature_cols].astype(float).values
        y = data["label"].astype(int).values

        split = int(len(data) * 0.8)
        split = max(50, min(split, len(data) - 20))
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        model = create_ml_classifier(model_type)
        if str(model_type).lower() not in {"logreg", "random_forest", "gbdt"}:
            model_type = "logreg"

        model.fit(X_train, y_train)
        prob_test = model.predict_proba(X_test)[:, 1]
        pred_test = (prob_test >= threshold).astype(int)
        acc = float(accuracy_score(y_test, pred_test))
        try:
            auc = float(roc_auc_score(y_test, prob_test))
        except Exception:
            auc = None

        signals = []
        for row in latest_rows:
            x = row[feature_cols].astype(float).values.reshape(1, -1)
            up_prob = float(model.predict_proba(x)[0, 1])
            signal = "buy" if up_prob >= threshold else ("sell" if up_prob <= (1 - threshold) else "hold")
            signals.append({
                "symbol": row["symbol"],
                "date": row["date"],
                "up_probability": round(up_prob, 4),
                "signal": signal,
                "expected_horizon_days": horizon_days,
            })
        signals.sort(key=lambda x: x["up_probability"], reverse=True)

        result = {
            "model": {
                "type": model_type,
                "feature_columns": feature_cols,
                "lookback_days": lookback_days,
                "horizon_days": horizon_days,
                "label_transaction_cost_bps": transaction_cost_bps,
                "threshold": threshold,
            },
            "dataset": {
                "symbols_requested": len(symbols),
                "symbols_used": len({x["symbol"] for x in signals}),
                "train_samples": int(len(X_train)),
                "test_samples": int(len(X_test)),
            },
            "evaluation": {
                "accuracy": round(acc, 4),
                "auc": round(auc, 4) if auc is not None else None,
            },
            "schedule": {
                "rebalance": rebalance,
                "next_rebalance_at": _next_rebalance_time(rebalance),
            },
            "signals": signals,
            "cache": {
                "hit": False,
                "cache_minutes": cache_minutes,
            },
            "note": "仅用于研究和策略评估，不构成投资建议。",
        }
        _cache_set("run_ml_strategy", args, result, cache_minutes)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"运行机器学习策略失败: {str(e)}")]


async def build_factor_model(args: dict) -> list[types.TextContent]:
    """构建因子模型"""
    try:
        import numpy as np
        import pandas as pd

        symbols = _normalize_symbol_list(args.get("symbols"))
        if not symbols:
            return [types.TextContent(type="text", text="symbols 不能为空")]
        factors = args.get("factors") or ["momentum", "volatility", "ma_gap", "rsi"]
        factors = [str(f).strip().lower() for f in factors]
        lookback_days = max(120, min(int(args.get("lookback_days", 365)), 3650))
        horizon_days = max(1, min(int(args.get("horizon_days", 5)), 30))
        top_n = max(3, min(int(args.get("top_n", 10)), 50))
        cache_minutes = max(0, min(int(args.get("cache_minutes", 15)), 24 * 60))
        rebalance = str(args.get("rebalance", "weekly")).lower()
        force_refresh = bool(args.get("force_refresh", False))
        if rebalance not in {"none", "daily", "weekly", "monthly"}:
            rebalance = "weekly"

        if cache_minutes > 0 and not force_refresh:
            cached = _cache_get("build_factor_model", args)
            if cached is not None:
                cached["cache"] = {
                    "hit": True,
                    "cache_minutes": cache_minutes,
                    "rebalance": rebalance,
                    "next_rebalance_at": _next_rebalance_time(rebalance),
                }
                return [types.TextContent(type="text", text=json.dumps(cached, indent=2, ensure_ascii=False))]

        panel = {}
        for s in symbols:
            df = _build_feature_frame(s, lookback_days, horizon_days)
            if df is None or len(df) < 60:
                continue
            panel[s] = df
        if len(panel) < 3:
            return [types.TextContent(type="text", text="有效股票不足（至少3只）")]

        factor_map = {
            "momentum": "momentum_20",
            "volatility": "volatility_20",
            "ma_gap": "ma_gap",
            "rsi": "rsi",
        }
        selected = [f for f in factors if f in factor_map]
        if not selected:
            selected = ["momentum", "volatility", "ma_gap", "rsi"]

        # 计算历史IC（按日期横截面）
        date_union = sorted(set().union(*[set(df["date"].tolist()) for df in panel.values()]))
        ic_series = {f: [] for f in selected}
        rank_ic_series = {f: [] for f in selected}
        for d in date_union:
            for f in selected:
                fac_vals = []
                fwd_vals = []
                col = factor_map[f]
                for s, df in panel.items():
                    row = df[df["date"] == d]
                    if row.empty:
                        continue
                    fv = float(row.iloc[0][col])
                    rv = float(row.iloc[0]["future_ret"])
                    if np.isfinite(fv) and np.isfinite(rv):
                        fac_vals.append(fv)
                        fwd_vals.append(rv)
                if len(fac_vals) >= 3:
                    ic = _safe_corr(fac_vals, fwd_vals)
                    if ic is not None:
                        ic_series[f].append(ic)
                    rank_ic = _safe_corr(pd.Series(fac_vals).rank().tolist(), pd.Series(fwd_vals).rank().tolist())
                    if rank_ic is not None:
                        rank_ic_series[f].append(rank_ic)

        factor_stats = []
        for f in selected:
            ics = ic_series[f]
            rics = rank_ic_series[f]
            factor_stats.append({
                "factor": f,
                "ic_mean": round(float(np.mean(ics)), 4) if ics else None,
                "ic_ir": round(float(np.mean(ics) / np.std(ics)), 4) if len(ics) > 1 and np.std(ics) > 0 else None,
                "rank_ic_mean": round(float(np.mean(rics)), 4) if rics else None,
                "samples": len(ics),
            })

        # 最新因子打分
        latest_rows = []
        for s, df in panel.items():
            row = df.iloc[-1]
            latest_rows.append({
                "symbol": s,
                "momentum": float(row["momentum_20"]),
                "volatility": float(row["volatility_20"]),
                "ma_gap": float(row["ma_gap"]),
                "rsi": float(row["rsi"]),
            })
        latest_df = pd.DataFrame(latest_rows)

        # 因子标准化后合成分数（波动率取反）
        z = pd.DataFrame()
        for f in selected:
            series = latest_df[f].astype(float)
            std = float(series.std()) if float(series.std()) > 0 else 1.0
            z[f] = (series - float(series.mean())) / std
        if "volatility" in z.columns:
            z["volatility"] = -z["volatility"]
        latest_df["score"] = z.sum(axis=1)
        latest_df = latest_df.sort_values("score", ascending=False)

        top = latest_df.head(top_n).to_dict(orient="records")

        result = {
            "config": {
                "lookback_days": lookback_days,
                "horizon_days": horizon_days,
                "factors": selected,
                "universe_size": len(panel),
            },
            "schedule": {
                "rebalance": rebalance,
                "next_rebalance_at": _next_rebalance_time(rebalance),
            },
            "factor_statistics": factor_stats,
            "ranked_stocks": top,
            "cache": {
                "hit": False,
                "cache_minutes": cache_minutes,
            },
            "note": "score 为标准化因子线性合成分数，波动率因子按风险惩罚处理。",
        }
        _cache_set("build_factor_model", args, result, cache_minutes)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"构建因子模型失败: {str(e)}")]


async def optimize_portfolio(args: dict) -> list[types.TextContent]:
    """组合优化"""
    try:
        import numpy as np
        import pandas as pd

        symbols = _normalize_symbol_list(args.get("symbols"))
        if len(symbols) < 2:
            return [types.TextContent(type="text", text="symbols 至少需要2只股票")]

        method = str(args.get("method", "mean_variance")).lower()
        lookback_days = max(120, min(int(args.get("lookback_days", 252)), 3650))
        risk_aversion = max(0.1, min(float(args.get("risk_aversion", 3.0)), 20.0))
        min_w = float(args.get("min_weight", 0.0))
        max_w = float(args.get("max_weight", 0.4))
        cache_minutes = max(0, min(int(args.get("cache_minutes", 15)), 24 * 60))
        rebalance = str(args.get("rebalance", "monthly")).lower()
        force_refresh = bool(args.get("force_refresh", False))
        if rebalance not in {"none", "daily", "weekly", "monthly"}:
            rebalance = "monthly"

        if cache_minutes > 0 and not force_refresh:
            cached = _cache_get("optimize_portfolio", args)
            if cached is not None:
                cached["cache"] = {
                    "hit": True,
                    "cache_minutes": cache_minutes,
                    "rebalance": rebalance,
                    "next_rebalance_at": _next_rebalance_time(rebalance),
                }
                return [types.TextContent(type="text", text=json.dumps(cached, indent=2, ensure_ascii=False))]

        if min_w < 0 or max_w <= 0 or min_w >= max_w:
            return [types.TextContent(type="text", text="权重约束非法：需满足 0 <= min_weight < max_weight")]
        if min_w * len(symbols) > 1.0:
            return [types.TextContent(type="text", text="权重约束不可行：min_weight * 股票数 > 1")]
        if max_w * len(symbols) < 1.0:
            return [types.TextContent(type="text", text="权重约束不可行：max_weight * 股票数 < 1")]

        ret_map = {}
        for s in symbols:
            bars = _fetch_bars(s, lookback_days)
            if len(bars) < 80:
                continue
            closes = pd.Series([float(b.close) for b in bars])
            rets = closes.pct_change().dropna()
            if len(rets) >= 60:
                ret_map[s] = rets.reset_index(drop=True)
        if len(ret_map) < 2:
            return [types.TextContent(type="text", text="可用历史数据不足，无法优化组合")]

        ret_df = pd.DataFrame(ret_map).dropna(how="any")
        if len(ret_df) < 40:
            return [types.TextContent(type="text", text="收益率样本不足，无法优化组合")]

        cols = ret_df.columns.tolist()
        mu = ret_df.mean().values * 252.0
        cov = ret_df.cov().values * 252.0
        n = len(cols)
        cov_reg = cov + np.eye(n) * 1e-6

        if method == "risk_parity":
            vol = np.sqrt(np.diag(cov_reg))
            base = 1.0 / np.where(vol > 0, vol, 1.0)
            w = _project_weights(base, min_w, max_w)
            method = "risk_parity"
        else:
            method = "mean_variance"
            raw = np.linalg.pinv(cov_reg).dot(mu / risk_aversion)
            w = _project_weights(raw, min_w, max_w)

        port_ret = float(np.dot(w, mu))
        port_vol = float(np.sqrt(np.dot(w, cov_reg.dot(w))))
        sharpe = (port_ret / port_vol) if port_vol > 0 else None
        mrc = cov_reg.dot(w)
        rc = w * mrc
        rc_sum = float(rc.sum()) if float(rc.sum()) != 0 else 1.0

        weights = []
        for i, s in enumerate(cols):
            weights.append({
                "symbol": s,
                "weight": round(float(w[i]), 6),
                "expected_return_annual": round(float(mu[i]), 4),
                "risk_contribution_pct": round(float(rc[i] / rc_sum * 100), 2),
            })
        weights.sort(key=lambda x: x["weight"], reverse=True)

        result = {
            "method": method,
            "constraints": {
                "min_weight": min_w,
                "max_weight": max_w,
            },
            "universe": {
                "requested_symbols": symbols,
                "used_symbols": cols,
                "sample_days": int(len(ret_df)),
            },
            "portfolio": {
                "expected_return_annual": round(port_ret, 4),
                "expected_volatility_annual": round(port_vol, 4),
                "expected_sharpe": round(sharpe, 4) if sharpe is not None else None,
            },
            "schedule": {
                "rebalance": rebalance,
                "next_rebalance_at": _next_rebalance_time(rebalance),
            },
            "weights": weights,
            "cache": {
                "hit": False,
                "cache_minutes": cache_minutes,
            },
            "note": "均值方差为近似解并执行权重投影；结果用于研究，不构成投资建议。",
        }
        _cache_set("optimize_portfolio", args, result, cache_minutes)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"组合优化失败: {str(e)}")]


# ============================================================
# 风控工具实现
# ============================================================

async def check_risk(args: dict) -> list[types.TextContent]:
    """风控检查"""
    try:
        symbol, action, quantity = args["symbol"], args["action"], int(args["quantity"])
        price = args["price"]
        
        # 获取账户信息
        bl = trade_ctx.account_balance()
        b = bl[0] if bl else None
        ta = float(b.net_assets) if b else 0
        ac = float(b.buy_power) if b else 0
        
        # 获取现有持仓
        ev = 0.0
        for ch in trade_ctx.stock_positions().channels:
            for p in ch.positions:
                if p.symbol == symbol:
                    try:
                        q = quote_ctx.quote([symbol])
                        cur = float(q[0].last_done) if q else float(p.cost_price)
                    except:
                        cur = float(p.cost_price)
                    ev = cur * float(p.quantity)
        
        rr = get_manager().full_check_before_order(
            symbol=symbol, action=action, quantity=quantity, price=price,
            total_assets=ta, available_cash=ac, existing_position_value=ev
        )
        
        return [types.TextContent(
            type="text",
            text=json.dumps(rr, indent=2, ensure_ascii=False)
        )]
    except Exception as e:
        return [types.TextContent(type="text", text=f"风控检查失败: {str(e)}")]


async def get_risk_config() -> list[types.TextContent]:
    """获取风控配置"""
    try:
        cfg = load_config()
        return [types.TextContent(
            type="text",
            text=json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False)
        )]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取配置失败: {str(e)}")]


async def set_risk_config(args: dict) -> list[types.TextContent]:
    """修改风控配置"""
    try:
        cfg = load_config()
        for k, v in args.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        save_config(cfg)
        return [types.TextContent(
            type="text",
            text=json.dumps({"修改成功": cfg.to_dict()}, indent=2, ensure_ascii=False)
        )]
    except Exception as e:
        return [types.TextContent(type="text", text=f"修改配置失败: {str(e)}")]


async def scan_stop_loss() -> list[types.TextContent]:
    """扫描止损"""
    try:
        mgr = get_manager()
        results = []
        
        for ch in trade_ctx.stock_positions().channels:
            for pos in ch.positions:
                try:
                    q = quote_ctx.quote([pos.symbol])
                    cur = float(q[0].last_done) if q else 0
                except:
                    cur = 0
                
                if cur > 0:
                    check = mgr.check_stop_loss(
                        symbol=pos.symbol,
                        cost_price=float(pos.cost_price),
                        current_price=cur,
                        quantity=float(pos.quantity),
                    )
                    results.append({
                        "股票": check.symbol,
                        "数量": check.quantity,
                        "成本价": check.cost_price,
                        "当前价": check.current_price,
                        "浮动盈亏": f"{check.loss_pct*100:.2f}%",
                        "止损线": f"{check.threshold_pct*100:.2f}%",
                        "是否触发": "⚠️ 已触发止损" if check.should_stop else "✅ 正常",
                    })
        
        return [types.TextContent(
            type="text",
            text=json.dumps({"止损扫描": results, "时间": datetime.now().isoformat()}, indent=2, ensure_ascii=False)
        )]
    except Exception as e:
        return [types.TextContent(type="text", text=f"扫描失败: {str(e)}")]


async def emergency_flatten(symbol: str | None) -> list[types.TextContent]:
    """紧急平仓"""
    try:
        from longport.openapi import OrderSide, OrderType, TimeInForceType
        
        targets = []
        for ch in trade_ctx.stock_positions().channels:
            for pos in ch.positions:
                if not symbol or pos.symbol == symbol:
                    targets.append({"symbol": pos.symbol, "quantity": int(float(pos.quantity))})
        
        if not targets:
            return [types.TextContent(type="text", text="没有可平仓的持仓")]
        
        results = []
        for t in targets:
            try:
                resp = trade_ctx.submit_order(
                    symbol=t["symbol"],
                    order_type=OrderType.MO,
                    side=OrderSide.Sell,
                    submitted_quantity=t["quantity"],
                    time_in_force=TimeInForceType.Day,
                )
                results.append({"股票": t["symbol"], "状态": f"✅ 已提交 {resp.order_id}"})
            except Exception as e:
                results.append({"股票": t["symbol"], "状态": f"❌ {e}"})
        
        return [types.TextContent(type="text",
            text=json.dumps({"紧急平仓": results, "时间": datetime.now().isoformat()}, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"紧急平仓失败: {str(e)}")]


# ============================================================
# 原有工具（行情 / 账户 / 交易）
# ============================================================

def _pq(pq) -> dict | None:
    if pq is None: return None
    try:
        last, prev = float(pq.last_done), float(pq.prev_close)
        chg = ((last-prev)/prev*100) if prev else 0
        return {"最新价": last, "昨收价": prev, "涨跌幅": f"{chg:+.2f}%",
                "最高价": float(pq.high), "最低价": float(pq.low),
                "成交量": pq.volume, "成交额": float(pq.turnover),
                "时间戳": pq.timestamp.isoformat() if pq.timestamp else None}
    except Exception: return None


async def get_account_info() -> list[types.TextContent]:
    try:
        bl = trade_ctx.account_balance()
        if not bl: return [types.TextContent(type="text", text="账户信息为空")]
        b = bl[0]
        pos = trade_ctx.stock_positions()
        cnt, val = 0, 0.0
        for ch in pos.channels:
            for p in ch.positions:
                cnt += 1; val += float(p.quantity)*float(p.cost_price)
        r = {"账户信息": {"可用现金": float(b.buy_power), "总资产": float(b.net_assets),
             "货币": b.currency, "持仓数量": cnt, "持仓总值": round(val,2),
             "更新时间": datetime.now().isoformat()}}
        return [types.TextContent(type="text", text=json.dumps(r,indent=2,ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取账户失败: {str(e)}")]


async def get_market_data(symbol: str) -> list[types.TextContent]:
    try:
        qs = quote_ctx.quote([symbol])
        if not qs: return [types.TextContent(type="text", text=f"未找到 {symbol}")]
        q = qs[0]; last, prev = float(q.last_done), float(q.prev_close)
        chg = ((last-prev)/prev*100) if prev else 0
        r = {"股票代码": symbol,
             "正常盘行情": {"最新价": last, "开盘价": float(q.open),
                "最高价": float(q.high), "最低价": float(q.low), "昨收价": prev,
                "涨跌幅": f"{chg:+.2f}%", "成交量": q.volume,
                "交易状态": str(q.trade_status), "更新时间": q.timestamp.isoformat()},
             "盘前行情":  _pq(getattr(q,"pre_market_quote",None))  or "暂无",
             "盘后/夜盘": _pq(getattr(q,"post_market_quote",None)) or "暂无"}
        return [types.TextContent(type="text", text=json.dumps(r,indent=2,ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取行情失败: {str(e)}")]


async def analyze_stock(symbol: str) -> list[types.TextContent]:
    try:
        from longport.openapi import Period, AdjustType
        import numpy as np
        end, start = date.today(), date.today()-timedelta(days=90)
        cs = quote_ctx.history_candlesticks_by_date(
            symbol=symbol,
            period=Period.Day,
            adjust_type=AdjustType.ForwardAdjust,
            start=start,
            end=end,
            trade_sessions=TradeSessions.All,
        )
        if not cs or len(cs)<20:
            return [types.TextContent(type="text", text="数据不足")]
        closes = [float(c.close) for c in cs]
        ma5, ma20 = round(sum(closes[-5:])/5,2), round(sum(closes[-20:])/20,2)
        diff = np.diff(closes[-15:])
        g, l = diff[diff>0], -diff[diff<0]
        ag, al = (float(np.mean(g)) if len(g) else 0), (float(np.mean(l)) if len(l) else 0)
        rsi = round(100-100/(1+ag/al),2) if al else 100
        trend = "上升" if ma5>ma20 else "下降"
        rsi_s = "超卖" if rsi<30 else ("超买" if rsi>70 else "中性")
        rec   = "买入" if (trend=="上升" and rsi<70) else ("卖出" if (trend=="下降" and rsi>30) else "观望")
        r = {"股票代码": symbol, "技术分析": {
            "当前价格": closes[-1], "MA5": ma5, "MA20": ma20, "RSI": rsi,
            "趋势": trend, "RSI信号": rsi_s, "交易建议": rec,
            "分析时间": datetime.now().isoformat()}}
        return [types.TextContent(type="text", text=json.dumps(r,indent=2,ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"分析失败: {str(e)}")]


async def get_historical_bars(args: dict) -> list[types.TextContent]:
    """获取历史K线"""
    try:
        from longport.openapi import Period, AdjustType
        symbol = args["symbol"]
        period_text = str(args.get("period", "1d")).lower()
        days = int(args.get("days", 180))
        limit = int(args.get("limit", 200))
        adjust_text = str(args.get("adjust_type", "forward")).lower()

        period_map = {
            "1m": Period.Min_1,
            "5m": Period.Min_5,
            "10m": Period.Min_10,
            "15m": Period.Min_15,
            "30m": Period.Min_30,
            "60m": Period.Min_60,
            "1h": Period.Min_60,
            "2h": Period.Min_120,
            "4h": Period.Min_240,
            "1d": Period.Day,
            "1w": Period.Week,
            "1mo": Period.Month,
        }
        period = period_map.get(period_text, Period.Day)
        adjust_type = AdjustType.NoAdjust if adjust_text == "none" else AdjustType.ForwardAdjust

        days = max(1, min(days, 3650))
        limit = max(1, min(limit, 2000))
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        candles = quote_ctx.history_candlesticks_by_date(
            symbol=symbol,
            period=period,
            adjust_type=adjust_type,
            start=start_date,
            end=end_date,
            trade_sessions=TradeSessions.All,
        )

        data = []
        for c in candles:
            data.append({
                "timestamp": c.timestamp.isoformat() if c.timestamp else None,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
                "turnover": float(c.turnover),
            })
        if len(data) > limit:
            data = data[-limit:]

        result = {
            "symbol": symbol,
            "period": period_text,
            "adjust_type": adjust_text,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "limit": limit,
            "count": len(data),
            "bars": data,
        }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取历史K线失败: {str(e)}")]


async def get_financials(symbol: str) -> list[types.TextContent]:
    """获取估值/财务相关数据"""
    try:
        from longport.openapi import CalcIndex
        static_list = quote_ctx.static_info([symbol])
        index_list = quote_ctx.calc_indexes(
            [symbol],
            [
                CalcIndex.PeTtmRatio,
                CalcIndex.PbRatio,
                CalcIndex.DividendRatioTtm,
                CalcIndex.TotalMarketValue,
            ],
        )
        if not static_list or not index_list:
            return [types.TextContent(type="text", text=f"未获取到 {symbol} 的财务数据")]

        st = static_list[0]
        idx = index_list[0]
        roe = None
        try:
            if getattr(st, "eps_ttm", None) and getattr(st, "bps", None):
                bps = float(st.bps)
                eps_ttm = float(st.eps_ttm)
                if bps != 0:
                    roe = round((eps_ttm / bps) * 100, 2)
        except Exception:
            roe = None

        result = {
            "股票代码": symbol,
            "公司信息": {
                "中文名": getattr(st, "name_cn", None),
                "英文名": getattr(st, "name_en", None),
                "交易所": getattr(st, "exchange", None),
                "货币": getattr(st, "currency", None),
                "每手股数": getattr(st, "lot_size", None),
            },
            "估值指标": {
                "PE_TTM": float(idx.pe_ttm_ratio) if getattr(idx, "pe_ttm_ratio", None) is not None else None,
                "PB": float(idx.pb_ratio) if getattr(idx, "pb_ratio", None) is not None else None,
                "股息率_TTM(%)": float(idx.dividend_ratio_ttm) if getattr(idx, "dividend_ratio_ttm", None) is not None else None,
                "总市值": float(idx.total_market_value) if getattr(idx, "total_market_value", None) is not None else None,
            },
            "财务指标": {
                "EPS": float(st.eps) if getattr(st, "eps", None) is not None else None,
                "EPS_TTM": float(st.eps_ttm) if getattr(st, "eps_ttm", None) is not None else None,
                "BPS": float(st.bps) if getattr(st, "bps", None) is not None else None,
                "ROE_估算(%)": roe,
            },
            "说明": "ROE 为基于 EPS_TTM/BPS 的近似估算值，仅供参考。",
        }
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取财务数据失败: {str(e)}")]


async def get_option_chain(args: dict) -> list[types.TextContent]:
    """获取期权链"""
    try:
        result = fetch_option_chain(
            quote_ctx=quote_ctx,
            symbol=str(args["symbol"]),
            expiry_date=args.get("expiry_date"),
            min_strike=args.get("min_strike"),
            max_strike=args.get("max_strike"),
            standard_only=bool(args.get("standard_only", False)),
            limit=int(args.get("limit", 100)),
            offset=int(args.get("offset", 0)),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取期权链失败: {str(e)}")]


async def get_option_expiries(symbol: str) -> list[types.TextContent]:
    try:
        result = fetch_option_expiries(quote_ctx, symbol)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取期权到期日失败: {str(e)}")]


async def estimate_option_order_fee(args: dict) -> list[types.TextContent]:
    try:
        legs = build_order_legs(
            legs=args.get("legs"),
            symbol=args.get("symbol"),
            side=args.get("side"),
            contracts=args.get("contracts"),
            price=args.get("price"),
        )
        fee = estimate_option_fee_for_legs(legs)
        return [types.TextContent(type="text", text=json.dumps(fee, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"估算期权费用失败: {str(e)}")]


def _available_cash() -> float:
    bl = trade_ctx.account_balance()
    b = bl[0] if bl else None
    return float(b.buy_power) if b else 0.0


async def submit_option_order_single_leg(args: dict) -> list[types.TextContent]:
    try:
        legs = build_order_legs(
            symbol=args.get("symbol"),
            side=args.get("side"),
            contracts=args.get("contracts"),
            price=args.get("price"),
        )
        ret = submit_option_order_with_risk(
            trade_ctx=trade_ctx,
            legs=legs,
            available_cash=_available_cash(),
            max_loss_threshold=args.get("max_loss_threshold"),
            max_capital_usage=args.get("max_capital_usage"),
        )
        if ret.get("blocked"):
            return [types.TextContent(type="text", text=json.dumps({"blocked": True, "risk": ret.get("risk")}, indent=2, ensure_ascii=False))]
        return [types.TextContent(type="text", text=json.dumps({"order": ret.get("order"), "risk": ret.get("risk")}, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"提交期权单腿订单失败: {str(e)}")]


async def submit_option_order_multi_leg(args: dict) -> list[types.TextContent]:
    try:
        legs = build_order_legs(legs=args.get("legs"))
        ret = submit_option_order_with_risk(
            trade_ctx=trade_ctx,
            legs=legs,
            available_cash=_available_cash(),
            max_loss_threshold=args.get("max_loss_threshold"),
            max_capital_usage=args.get("max_capital_usage"),
        )
        if ret.get("blocked"):
            return [types.TextContent(type="text", text=json.dumps({"blocked": True, "risk": ret.get("risk")}, indent=2, ensure_ascii=False))]
        return [types.TextContent(type="text", text=json.dumps({"result": ret.get("result"), "risk": ret.get("risk")}, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"提交期权多腿订单失败: {str(e)}")]


async def get_option_positions() -> list[types.TextContent]:
    try:
        ret = svc_get_option_positions(trade_ctx, quote_ctx)
        return [types.TextContent(type="text", text=json.dumps(ret, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取期权持仓失败: {str(e)}")]


async def get_option_orders(status: str = "all") -> list[types.TextContent]:
    try:
        ret = svc_get_option_orders(trade_ctx, status=status)
        return [types.TextContent(type="text", text=json.dumps(ret, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取期权订单失败: {str(e)}")]


async def get_intraday(symbol: str) -> list[types.TextContent]:
    """获取分时数据"""
    try:
        lines = quote_ctx.intraday(symbol)
        data = []
        for x in lines:
            data.append({
                "timestamp": x.timestamp.isoformat() if getattr(x, "timestamp", None) else None,
                "price": float(x.price),
                "avg_price": float(x.avg_price),
                "volume": float(x.volume),
                "turnover": float(x.turnover),
            })
        result = {"symbol": symbol, "count": len(data), "intraday": data}
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取分时数据失败: {str(e)}")]


async def get_watchlist() -> list[types.TextContent]:
    """获取自选股"""
    try:
        groups = quote_ctx.watchlist()
        result = []
        for g in groups:
            securities = []
            for s in getattr(g, "securities", []):
                securities.append({
                    "symbol": getattr(s, "symbol", None),
                    "name": getattr(s, "name", None),
                    "market": str(getattr(s, "market", "")),
                    "watched_price": float(s.watched_price) if getattr(s, "watched_price", None) is not None else None,
                    "watched_at": s.watched_at.isoformat() if getattr(s, "watched_at", None) else None,
                })
            result.append({
                "group_id": getattr(g, "id", None),
                "group_name": getattr(g, "name", ""),
                "count": len(securities),
                "securities": securities,
            })
        return [types.TextContent(type="text", text=json.dumps({"watchlists": result}, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取自选股失败: {str(e)}")]


async def submit_order(args: dict) -> list[types.TextContent]:
    try:
        from longport.openapi import OrderSide, OrderType, TimeInForceType
        from decimal import Decimal
        symbol, action, quantity = args["symbol"], args["action"], int(args["quantity"])
        price = args.get("price")
        if action == "buy":
            cp = float(price) if price else 0
            if not cp:
                try:
                    qs = quote_ctx.quote([symbol]); cp = float(qs[0].last_done) if qs else 0
                except Exception: pass
            if cp:
                bl = trade_ctx.account_balance(); b = bl[0] if bl else None
                ta = float(b.net_assets) if b else 0; ac = float(b.buy_power) if b else 0
                ev = 0.0
                for ch in trade_ctx.stock_positions().channels:
                    for p in ch.positions:
                        if p.symbol == symbol:
                            try: q=quote_ctx.quote([symbol]); cur=float(q[0].last_done) if q else float(p.cost_price)
                            except: cur=float(p.cost_price)
                            ev = cur*float(p.quantity)
                rr = get_manager().full_check_before_order(
                    symbol=symbol, action=action, quantity=quantity, price=cp,
                    total_assets=ta, available_cash=ac, existing_position_value=ev)
                if not rr["passed"]:
                    return [types.TextContent(type="text", text=json.dumps({
                        "订单被风控拦截": True, "原因": rr["blocks"],
                        "建议": rr["summary"]}, indent=2, ensure_ascii=False))]
        side = OrderSide.Buy if action=="buy" else OrderSide.Sell
        resp = trade_ctx.submit_order(
            symbol=symbol,
            order_type=OrderType.LO if price else OrderType.MO,
            side=side, submitted_quantity=quantity,
            time_in_force=TimeInForceType.Day,
            **({} if not price else {"submitted_price": Decimal(str(price))}))
        r = {"订单已提交": {"订单ID": resp.order_id, "股票": symbol,
             "动作": action, "数量": quantity,
             "价格": price if price else "市价",
             "时间": datetime.now().isoformat(), "风控": "✅ 通过"}}
        return [types.TextContent(type="text", text=json.dumps(r,indent=2,ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"下单失败: {str(e)}")]


async def get_positions() -> list[types.TextContent]:
    try:
        lst = []
        for ch in trade_ctx.stock_positions().channels:
            for pos in ch.positions:
                try: q=quote_ctx.quote([pos.symbol]); cur=float(q[0].last_done) if q else 0
                except: cur=0
                cost=float(pos.cost_price)*float(pos.quantity); val=cur*float(pos.quantity)
                pnl=val-cost; pp=(pnl/cost*100) if cost else 0
                lst.append({"股票": pos.symbol, "数量": float(pos.quantity),
                    "成本价": float(pos.cost_price), "当前价": cur,
                    "盈亏": round(pnl,2), "盈亏%": f"{pp:.2f}%"})
        return [types.TextContent(type="text",
            text=json.dumps({"持仓": lst, "总数": len(lst)}, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取持仓失败: {str(e)}")]


async def get_orders(status="all") -> list[types.TextContent]:
    try:
        allowed = {"active":{"New","PartialFilled"},"filled":{"Filled"},"cancelled":{"Canceled"}}.get(status)
        lst = []
        for o in trade_ctx.today_orders():
            s = str(o.status)
            if allowed and s not in allowed: continue
            lst.append({"ID": o.order_id, "股票": o.symbol, "状态": s,
                "方向": str(o.side), "数量": o.quantity, "已成交": o.executed_quantity,
                "价格": float(o.price) if o.price else "市价"})
        return [types.TextContent(type="text",
            text=json.dumps({"订单": lst, "总数": len(lst)}, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"获取订单失败: {str(e)}")]


async def cancel_order(order_id: str) -> list[types.TextContent]:
    try:
        trade_ctx.cancel_order(order_id)
        return [types.TextContent(type="text",
            text=json.dumps({"取消成功": order_id}, indent=2, ensure_ascii=False))]
    except Exception as e:
        return [types.TextContent(type="text", text=f"取消失败: {str(e)}")]


# ─── 启动（原生 framed JSON-RPC over stdio）─────────────────────────────
def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(x) for x in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _jsonable(dump())
        except Exception:
            pass
    to_dict = getattr(value, "dict", None)
    if callable(to_dict):
        try:
            return _jsonable(to_dict())
        except Exception:
            pass
    return str(value)


def _read_exact(stream: io.BufferedReader, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _read_framed_message(stream: io.BufferedReader) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if b":" not in line:
            continue
        key_raw, value_raw = line.split(b":", 1)
        headers[key_raw.strip().lower().decode("utf-8", errors="ignore")] = value_raw.strip().decode(
            "utf-8",
            errors="ignore",
        )
    length_raw = headers.get("content-length")
    if not length_raw:
        return None
    length = int(length_raw)
    body = _read_exact(stream, length)
    if len(body) != length:
        return None
    return json.loads(body.decode("utf-8"))


def _write_framed_message(stream: io.BufferedWriter, message: dict[str, Any]) -> None:
    # IMPORTANT: OpenClaw MCP parser currently treats string length as byte length.
    # Keep payload ASCII-escaped to make byte length == character length.
    body = json.dumps(message, ensure_ascii=True).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    stream.write(header)
    stream.write(body)
    stream.flush()


async def _handle_jsonrpc_request(message: dict[str, Any]) -> dict[str, Any] | None:
    req_id = message.get("id")
    method = str(message.get("method", "")).strip()
    params = message.get("params")
    params_dict = params if isinstance(params, dict) else {}

    # Notifications: no response
    if req_id is None:
        return None

    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "longport-trading", "version": "4.1.0"},
                },
            }

        if method in ("tools/list", "list_tools"):
            tools = await handle_list_tools()
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": _jsonable(tools)},
            }

        if method in ("tools/call", "call_tool"):
            # Do not start background jobs during discovery.
            # Only start them when the first real tool call arrives.
            _start_background_features_once()
            tool_name = str(params_dict.get("name", "")).strip()
            tool_args = params_dict.get("arguments")
            tool_args_dict = tool_args if isinstance(tool_args, dict) else {}
            result = await handle_call_tool(tool_name, tool_args_dict)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": _jsonable(result), "isError": False},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    except Exception as exc:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": str(exc)},
        }


async def main():
    if not _acquire_single_instance():
        return
    atexit.register(_release_single_instance)
    _write_pid_file()
    # Prevent accidental print()/stdout logs from breaking MCP framing.
    protocol_stdout = sys.stdout.buffer
    sys.stdout = sys.stderr
    try:
        stdin_buffer = sys.stdin.buffer
        while True:
            request = _read_framed_message(stdin_buffer)
            if request is None:
                break
            response = await _handle_jsonrpc_request(request)
            if response is not None:
                _write_framed_message(protocol_stdout, response)
    finally:
        _reset_contexts()
        _remove_pid_file()

if __name__ == "__main__":
    asyncio.run(main())
