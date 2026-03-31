import json
import os
import sys
import subprocess
import atexit
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Literal, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_DIR = os.path.join(ROOT, "mcp_server")
# 回测中心：服务器本地 K 线缓存（分批拉取合并后写入，供 compare 直接读取）
KLINE_SERVER_CACHE_DIR = os.path.join(ROOT, "data", "klines")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if MCP_DIR not in sys.path:
    sys.path.insert(0, MCP_DIR)

import logging
from runtime_process_utils import is_pid_alive, managed_subprocess_status, read_pid_file

logging.getLogger('asyncio').setLevel(logging.ERROR)

from config.live_settings import live_settings
from longport.openapi import (
    AdjustType,
    Config,
    OrderSide,
    OrderType,
    Period,
    QuoteContext,
    TimeInForceType,
    TradeContext,
    TradeSessions,
)
from mcp_server.backtest_engine import BacktestEngine, Bar, coerce_bar_datetime
from mcp_server.ml_common import (
    FEATURE_COLUMNS,
    build_ml_feature_frame,
    create_ml_classifier,
    walk_forward_probability_map,
)
from mcp_server.fee_model import (
    get_default_fee_schedule,
    get_fee_schedule,
    estimate_stock_order_fee,
    estimate_us_option_order_fee,
    set_fee_schedule,
)
from mcp_server.market_analysis import get_comprehensive_analysis, get_sector_rotation
from mcp_server.risk_manager import RiskConfig, get_manager, load_config, save_config, trade_value
from mcp_server.strategies import get_strategy, list_strategy_metadata, list_strategy_names
from mcp_server.options_service import (
    build_order_legs,
    estimate_option_fee_for_legs,
    submit_option_order_with_risk,
    fetch_option_expiries,
    fetch_option_chain,
    get_option_positions as svc_get_option_positions,
    get_option_orders as svc_get_option_orders,
    run_option_backtest as svc_run_option_backtest,
)
from api.auto_trader import AutoTraderService, make_feishu_sender, load_persisted_signals
from api.longport_history_gate import (
    PRIORITY_LOW,
    acquire_history_slot,
    longport_history_priority,
    release_history_slot,
    using_priority_param,
)
from api.notification_preferences import (
    load_notification_preferences,
)
from api.schemas_auto_trader import (
    AutoTraderConfirmBody,
    AutoTraderMlMatrixApplyBody,
    AutoTraderMlMatrixRunBody,
    AutoTraderResearchRunBody,
    AutoTraderStrategyMatrixRunBody,
)
from api.schemas_backtest import BacktestBarItem, BacktestKline
from api.schemas_options_trade import SubmitOrderBody
from api.auto_trader_research import (
    get_factor_ab_report,
    get_factor_ab_report_markdown,
    get_ml_param_matrix_result,
    get_model_compare,
    get_research_snapshot,
    get_research_status,
    get_strategy_param_matrix_result,
    get_research_snapshot_history_result,
    ml_matrix_row_to_auto_trader_patch,
    list_research_snapshot_history,
    resolve_ml_matrix_row_for_apply,
    run_ml_param_matrix,
    run_research_snapshot,
    run_strategy_param_matrix,
)
from api.perf_metrics import emit_metric, read_recent_metrics
from api.research_data_provider import OpenBBClient
from api.services.longport_client_service import (
    can_try_context_init,
    is_longport_connect_error,
    mark_context_connect_failure,
    mark_context_connect_success,
    throttled_reset_contexts,
)
from api.services.runtime_state import get_runtime_state
from api.routers import (
    auto_trader_router,
    backtest_router,
    dashboard_market_router,
    fees_risk_router,
    notifications_router,
    options_trade_router,
    setup_router,
)


def create_contexts() -> tuple[QuoteContext, TradeContext]:
    live_settings.assert_longport_configured()
    cfg = Config(
        app_key=live_settings.LONGPORT_APP_KEY,
        app_secret=live_settings.LONGPORT_APP_SECRET,
        access_token=live_settings.LONGPORT_ACCESS_TOKEN,
        enable_overnight=True,
        enable_print_quote_packages=False,
    )
    return QuoteContext(cfg), TradeContext(cfg)


_RUNTIME_STATE = get_runtime_state()
_ctx_lock = _RUNTIME_STATE.ctx_lock
quote_ctx: Optional[QuoteContext] = _RUNTIME_STATE.quote_ctx
trade_ctx: Optional[TradeContext] = _RUNTIME_STATE.trade_ctx
LONGPORT_CONNECTION_LIMIT = max(1, int(os.getenv("LONGPORT_CONNECTION_LIMIT", "10")))
_longport_last_error: Optional[str] = _RUNTIME_STATE.longport_last_error
_longport_last_init_at: Optional[str] = _RUNTIME_STATE.longport_last_init_at
MCP_PID_FILE = os.path.join(MCP_DIR, ".longport_mcp.pid")
FEISHU_PID_FILE = os.path.join(MCP_DIR, ".feishu_bot.pid")
WATCHDOG_PID_FILE = os.path.join(ROOT, ".backend_watchdog.pid")
WATCHDOG_PAUSE_FILE = os.path.join(ROOT, ".backend_watchdog.pause")
WATCHDOG_BUSY_FILE = os.path.join(ROOT, ".backend_watchdog.busy")
AUTO_TRADER_PID_FILE = os.path.join(ROOT, ".auto_trader_worker.pid")
AUTO_TRADER_SUPERVISOR_PID_FILE = os.path.join(ROOT, ".auto_trader_supervisor.pid")
AUTO_TRADER_SUPERVISOR_STOP_FILE = os.path.join(ROOT, ".auto_trader_supervisor.stop")
AUTO_TRADER_SUPERVISOR_STATUS_FILE = os.path.join(ROOT, ".auto_trader_supervisor.status.json")
AUTO_TRADER_WORKER_RUNTIME_FILE = os.path.join(ROOT, ".auto_trader_worker.runtime.json")
AUTO_TRADER_WORKER_TRIGGER_SCAN_FILE = os.path.join(ROOT, ".auto_trader_worker.trigger_scan")
AUTO_TRADER_WORKER_CONFIRM_SIGNALS_FILE = os.path.join(ROOT, ".auto_trader_worker.confirm_signals.json")
WATCHDOG_LOG_FILE = os.path.join(ROOT, "launcher_watchdog.log")
_RESEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=1)
_RESEARCH_PROCESS_WORKERS = max(1, int(os.getenv("AUTO_TRADER_RESEARCH_PROCESS_WORKERS", "1")))
if os.name == "nt":
    # Windows 上强制关闭 research 进程池，避免 spawn 子进程导致终端窗口反复弹出/消失。
    _RESEARCH_USE_PROCESS_EXECUTOR = False
else:
    _RESEARCH_USE_PROCESS_EXECUTOR = str(
        os.getenv("AUTO_TRADER_RESEARCH_USE_PROCESS_EXECUTOR", "1")
    ).strip().lower() in {"1", "true", "yes", "on"}
_RESEARCH_PROCESS_EXECUTOR: ProcessPoolExecutor | None = None
_RESEARCH_PROCESS_EXECUTOR_LOCK = threading.RLock()
_RESEARCH_TASKS_LOCK = _RUNTIME_STATE.research_tasks_lock
_AUTO_TRADER_CONFIRM_QUEUE_LOCK = threading.RLock()
_AUTO_TRADER_PROCESS_OP_LOCK = threading.RLock()
_STARTUP_SIDE_EFFECTS_LOCK = threading.RLock()
_STARTUP_REVERSAL_WATCHER_STARTED = False
_STARTUP_AUTO_TRADER_BOOTSTRAPPED = False
_RESEARCH_TASKS: dict[str, dict[str, Any]] = _RUNTIME_STATE.research_tasks
_RESEARCH_TASK_MAX_KEEP = max(50, int(os.getenv("AUTO_TRADER_RESEARCH_TASK_MAX_KEEP", "200")))
_RESEARCH_TASK_MAX_PENDING = max(1, int(os.getenv("AUTO_TRADER_RESEARCH_TASK_MAX_PENDING", "3")))
_RESEARCH_BUSY_LOCK = _RUNTIME_STATE.research_busy_lock
_RESEARCH_BUSY_ACTIVE = _RUNTIME_STATE.research_busy_active
_RESEARCH_BUSY_HEARTBEAT_THREAD: threading.Thread | None = None
_RESEARCH_BUSY_HEARTBEAT_STOP = threading.Event()
_RESEARCH_BUSY_HEARTBEAT_INTERVAL_SECONDS = max(
    5.0, float(os.getenv("AUTO_TRADER_RESEARCH_BUSY_HEARTBEAT_SECONDS", "15"))
)
_LONGPORT_INVALID_SYMBOL_CACHE_LOCK = threading.RLock()
_LONGPORT_INVALID_SYMBOL_CACHE_TTL_SECONDS = max(
    300, int(os.getenv("LONGPORT_INVALID_SYMBOL_CACHE_TTL_SECONDS", "21600"))
)
_LONGPORT_INVALID_SYMBOL_CACHE: dict[str, tuple[float, str]] = {}
_LONGPORT_BARS_MEM_CACHE_LOCK = threading.RLock()
_LONGPORT_BARS_MEM_CACHE: dict[str, tuple[float, list[Bar]]] = {}
_LONGPORT_BARS_MEM_CACHE_TTL_SECONDS = max(1, int(os.getenv("LONGPORT_BARS_MEM_CACHE_TTL_SECONDS", "15")))
_LONGPORT_BARS_MEM_CACHE_MAX_ENTRIES = max(100, int(os.getenv("LONGPORT_BARS_MEM_CACHE_MAX_ENTRIES", "3000")))
_LONGPORT_BARS_INFLIGHT_LOCK = threading.RLock()
_LONGPORT_BARS_INFLIGHT: dict[str, dict[str, Any]] = {}
_LONGPORT_BARS_INFLIGHT_HOLD_SECONDS = max(3, int(os.getenv("LONGPORT_BARS_INFLIGHT_HOLD_SECONDS", "8")))
_STRONG_STOCKS_CACHE_LOCK = threading.RLock()
_STRONG_STOCKS_CACHE: dict[str, dict[str, Any]] = {}
_STRONG_STOCKS_CACHE_TTL_SECONDS = max(3, int(os.getenv("AUTO_TRADER_STRONG_STOCKS_CACHE_TTL_SECONDS", "12")))
_STRONG_STOCKS_CACHE_STALE_SECONDS = max(
    _STRONG_STOCKS_CACHE_TTL_SECONDS, int(os.getenv("AUTO_TRADER_STRONG_STOCKS_CACHE_STALE_SECONDS", "180"))
)
_STRONG_STOCKS_SEMAPHORE = threading.BoundedSemaphore(max(1, int(os.getenv("AUTO_TRADER_STRONG_STOCKS_MAX_CONCURRENCY", "1"))))
_STRONG_STOCKS_REFRESH_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, int(os.getenv("AUTO_TRADER_STRONG_STOCKS_REFRESH_WORKERS", "1")))
)
_STRONG_STOCKS_REFRESH_LOCK = threading.RLock()
_STRONG_STOCKS_REFRESH_INFLIGHT: set[str] = set()
LONGPORT_GATEWAY_BASE_URL = str(os.getenv("LONGPORT_GATEWAY_BASE_URL", "")).strip().rstrip("/")
LONGPORT_GATEWAY_TIMEOUT_SECONDS = max(1.0, float(os.getenv("LONGPORT_GATEWAY_TIMEOUT_SECONDS", "8")))


def _gateway_enabled() -> bool:
    base = LONGPORT_GATEWAY_BASE_URL
    if not base:
        return False
    # 防止误配成本机同端口导致递归请求。
    return not (base.startswith("http://127.0.0.1:8000") or base.startswith("http://localhost:8000"))


def _gateway_get_json(path: str, params: dict | None = None, timeout: float | None = None) -> dict | None:
    if not _gateway_enabled():
        return None
    q = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{LONGPORT_GATEWAY_BASE_URL}{path}{q}"
    try:
        with urllib.request.urlopen(url, timeout=float(timeout or LONGPORT_GATEWAY_TIMEOUT_SECONDS)) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _gateway_post_json(path: str, payload: dict[str, Any], timeout: float | None = None) -> tuple[bool, dict[str, Any]]:
    if not _gateway_enabled():
        return False, {"error": "gateway_disabled"}
    url = f"{LONGPORT_GATEWAY_BASE_URL}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=float(timeout or LONGPORT_GATEWAY_TIMEOUT_SECONDS)) as resp:
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


def _win_subprocess_silent_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def _is_main_process_runtime() -> bool:
    try:
        return multiprocessing.current_process().name == "MainProcess"
    except Exception:
        return True


def _get_research_process_executor() -> ProcessPoolExecutor | None:
    global _RESEARCH_PROCESS_EXECUTOR
    if not _RESEARCH_USE_PROCESS_EXECUTOR:
        return None
    # 子进程（例如 ProcessPool worker）禁止再创建 pool，避免递归派生。
    if not _is_main_process_runtime():
        return None
    with _RESEARCH_PROCESS_EXECUTOR_LOCK:
        if _RESEARCH_PROCESS_EXECUTOR is None:
            _RESEARCH_PROCESS_EXECUTOR = ProcessPoolExecutor(max_workers=_RESEARCH_PROCESS_WORKERS)
        return _RESEARCH_PROCESS_EXECUTOR


def _shutdown_research_process_executor() -> None:
    global _RESEARCH_PROCESS_EXECUTOR
    with _RESEARCH_PROCESS_EXECUTOR_LOCK:
        if _RESEARCH_PROCESS_EXECUTOR is not None:
            try:
                _RESEARCH_PROCESS_EXECUTOR.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            _RESEARCH_PROCESS_EXECUTOR = None


def _close_context(ctx: Any) -> None:
    if ctx is None:
        return
    close_fn = getattr(ctx, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def ensure_contexts() -> tuple[QuoteContext, TradeContext]:
    global quote_ctx, trade_ctx, _longport_last_error, _longport_last_init_at
    with _ctx_lock:
        if _RUNTIME_STATE.quote_ctx is None or _RUNTIME_STATE.trade_ctx is None:
            if not can_try_context_init(_RUNTIME_STATE):
                _longport_last_error = "longport_connect_breaker_open"
                _RUNTIME_STATE.longport_last_error = _longport_last_error
                raise RuntimeError("longport_connect_breaker_open")
            try:
                _RUNTIME_STATE.quote_ctx, _RUNTIME_STATE.trade_ctx = create_contexts()
                quote_ctx, trade_ctx = _RUNTIME_STATE.quote_ctx, _RUNTIME_STATE.trade_ctx
                mark_context_connect_success(_RUNTIME_STATE)
                _longport_last_error = None
                _longport_last_init_at = datetime.now().isoformat()
                _RUNTIME_STATE.longport_last_error = _longport_last_error
                _RUNTIME_STATE.longport_last_init_at = _longport_last_init_at
            except Exception as e:
                _longport_last_error = str(e)
                _RUNTIME_STATE.longport_last_error = _longport_last_error
                mark_context_connect_failure(_RUNTIME_STATE, e)
                raise
        quote_ctx, trade_ctx = _RUNTIME_STATE.quote_ctx, _RUNTIME_STATE.trade_ctx
        return quote_ctx, trade_ctx


def reset_contexts() -> None:
    global quote_ctx, trade_ctx
    with _ctx_lock:
        _close_context(_RUNTIME_STATE.quote_ctx)
        _close_context(_RUNTIME_STATE.trade_ctx)
        _RUNTIME_STATE.quote_ctx = None
        _RUNTIME_STATE.trade_ctx = None
        quote_ctx = None
        trade_ctx = None


def _collect_longport_runtime_state() -> dict[str, Any]:
    with _ctx_lock:
        return {
            "quote_ready": _RUNTIME_STATE.quote_ctx is not None,
            "trade_ready": _RUNTIME_STATE.trade_ctx is not None,
            "last_error": _RUNTIME_STATE.longport_last_error,
            "last_init_at": _RUNTIME_STATE.longport_last_init_at,
            "connect_breaker_until_ts": float(_RUNTIME_STATE.longport_connect_breaker_until_ts or 0.0),
            "last_reset_ts": float(_RUNTIME_STATE.longport_last_reset_ts or 0.0),
        }


atexit.register(reset_contexts)
atexit.register(lambda: _RESEARCH_EXECUTOR.shutdown(wait=False, cancel_futures=True))
atexit.register(_shutdown_research_process_executor)
atexit.register(lambda: _STRONG_STOCKS_REFRESH_EXECUTOR.shutdown(wait=False, cancel_futures=True))

app = FastAPI(title="LongPort UI API", version="0.1.0")
CORS_ALLOW_ORIGINS = [
    s.strip()
    for s in str(
        os.getenv(
            "CORS_ALLOW_ORIGINS",
            "http://127.0.0.1:3000,http://localhost:3000",
        )
    ).split(",")
    if s.strip()
]
if not CORS_ALLOW_ORIGINS:
    CORS_ALLOW_ORIGINS = ["http://127.0.0.1:3000", "http://localhost:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 允许本地端口变更（例如 Next.js 从 3001 启动）时也可通过预检
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
)

def _translate_validation_error(err: dict[str, Any]) -> str:
    err_type = str(err.get("type", ""))
    msg = str(err.get("msg", "参数校验失败"))
    ctx = err.get("ctx") if isinstance(err.get("ctx"), dict) else {}
    inp = err.get("input")

    if err_type == "missing":
        return "必填参数缺失"
    if err_type == "greater_than_equal":
        ge = ctx.get("ge")
        return f"输入值需大于等于 {ge}" if ge is not None else "输入值过小"
    if err_type == "greater_than":
        gt = ctx.get("gt")
        return f"输入值需大于 {gt}" if gt is not None else "输入值过小"
    if err_type == "less_than_equal":
        le = ctx.get("le")
        return f"输入值需小于等于 {le}" if le is not None else "输入值过大"
    if err_type == "less_than":
        lt = ctx.get("lt")
        return f"输入值需小于 {lt}" if lt is not None else "输入值过大"
    if err_type == "literal_error":
        expected = ctx.get("expected")
        return f"取值不在允许范围内，允许值: {expected}" if expected is not None else "取值不在允许范围内"
    if err_type in {"int_parsing", "float_parsing"}:
        return "输入值格式错误，应为数字"
    if err_type == "string_type":
        return "输入值格式错误，应为字符串"
    if err_type == "bool_type":
        return "输入值格式错误，应为布尔值"
    if err_type == "list_type":
        return "输入值格式错误，应为数组"
    if inp is None and msg:
        return msg
    return msg or "参数校验失败"


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_request: Request, exc: RequestValidationError):
    items: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = list(err.get("loc", []))
        # loc 示例: ["body", "holding_days"]
        field_path = ".".join(str(x) for x in loc[1:]) if len(loc) > 1 else ".".join(str(x) for x in loc)
        items.append(
            {
                "field": field_path or "unknown",
                "message": _translate_validation_error(err),
                "input": err.get("input"),
                "type": err.get("type"),
            }
        )
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "请求参数校验失败，请检查输入后重试。",
            "details": items,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception):
    # 避免未捕获 500 走到默认错误中间件导致前端看见“CORS 缺失”假象
    logging.exception("Unhandled server exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "服务器内部错误，请稍后重试。",
            "detail": str(exc),
        },
    )

_managed_processes: dict[str, subprocess.Popen[Any]] = _RUNTIME_STATE.managed_processes


def _read_json_file(path: str) -> dict[str, Any]:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _read_watchdog_restart_events(limit: int = 20) -> list[dict[str, Any]]:
    lim = max(1, min(500, int(limit)))
    if not os.path.exists(WATCHDOG_LOG_FILE):
        return []
    items: list[dict[str, Any]] = []
    try:
        with open(WATCHDOG_LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = str(raw or "").strip()
                if not line:
                    continue
                row: dict[str, Any] = {}
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        row = parsed
                except Exception:
                    # 兼容旧版文本日志格式：[ts] message
                    row = {"event": "legacy_log", "message": line}
                event_name = str(row.get("event", ""))
                if event_name not in {"restart_attempt", "restart_success", "restart_failed", "restart_skip"}:
                    continue
                items.append(
                    {
                        "ts": row.get("ts"),
                        "event": event_name,
                        "reason_code": row.get("reason_code", ""),
                        "message": row.get("message", ""),
                        "fail_count": row.get("fail_count"),
                        "pid_before": row.get("pid_before"),
                        "pid_after": row.get("pid_after"),
                        "unknown_pids": row.get("unknown_pids"),
                        "error": row.get("error"),
                    }
                )
    except Exception:
        return []
    return items[-lim:]


def _remove_file_silent(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _stop_feishu_bot_managed_or_pidfile() -> str:
    """停止飞书指令机器人：优先终止本 API 拉起的子进程，否则按 pid 文件结束进程。"""
    p = _managed_processes.get("feishu_bot")
    if p is not None and p.poll() is None:
        try:
            p.terminate()
            try:
                p.wait(timeout=5.0)
            except Exception:
                pass
            _managed_processes.pop("feishu_bot", None)
            return "stopped"
        except Exception as e:
            return f"error: {e}"
    fpid = read_pid_file(FEISHU_PID_FILE)
    if is_pid_alive(fpid):
        if _terminate_pid(fpid):
            return "stopped"
        return "terminate_sent"
    return "not_running"


def _terminate_pid(pid: Optional[int], timeout_seconds: float = 5.0) -> bool:
    if not pid or not is_pid_alive(pid):
        return False
    if os.name == "nt":
        try:
            proc = subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                capture_output=True,
                text=True,
                **_win_subprocess_silent_kwargs(),
            )
            if proc.returncode != 0:
                return False
            deadline = time.time() + max(0.5, float(timeout_seconds))
            while time.time() < deadline:
                if not is_pid_alive(pid):
                    return True
                time.sleep(0.1)
            return not is_pid_alive(pid)
        except Exception:
            return False
    try:
        os.kill(pid, 15)
        return True
    except Exception:
        return False


def _list_python_pids_by_script(script_name: str) -> list[int]:
    if os.name != "nt":
        return []
    try:
        where = f"name='python.exe' and CommandLine like '%{script_name}%'"
        out = subprocess.check_output(  # noqa: S603
            ["wmic", "process", "where", where, "get", "ProcessId", "/value"],
            text=True,
            encoding="utf-8",
            errors="ignore",
            **_win_subprocess_silent_kwargs(),
        )
    except Exception:
        return []
    pids: set[int] = set()
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("ProcessId="):
            continue
        raw = line.split("=", 1)[-1].strip()
        if not raw.isdigit():
            continue
        pid = int(raw)
        if pid > 0 and pid != os.getpid():
            pids.add(pid)
    return sorted(pids)


def _pid_commandline(pid: Optional[int]) -> str:
    if not pid or os.name != "nt":
        return ""
    try:
        out = subprocess.check_output(  # noqa: S603
            ["wmic", "process", "where", f"processid={int(pid)}", "get", "CommandLine", "/value"],
            text=True,
            encoding="utf-8",
            errors="ignore",
            **_win_subprocess_silent_kwargs(),
        )
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("CommandLine="):
                return line.split("=", 1)[-1].strip().lower()
    except Exception:
        return ""
    return ""


def _is_auto_trader_script_pid(pid: Optional[int]) -> bool:
    cmd = _pid_commandline(pid)
    if not cmd:
        return False
    return ("auto_trader_worker.py" in cmd) or ("auto_trader_supervisor.py" in cmd)


def _cleanup_orphan_auto_trader_processes() -> int:
    total = 0
    for name in ("auto_trader_supervisor.py", "auto_trader_worker.py"):
        for pid in _list_python_pids_by_script(name):
            if _terminate_pid(pid):
                total += 1
    return total


def _is_auto_trader_supervisor_running() -> bool:
    p = _managed_processes.get("auto_trader_supervisor")
    running, _, _ = managed_subprocess_status(p, AUTO_TRADER_SUPERVISOR_PID_FILE)
    return running


def _is_auto_trader_worker_running() -> bool:
    pid = read_pid_file(AUTO_TRADER_PID_FILE)
    return is_pid_alive(pid)


def _auto_trader_runtime_status() -> dict[str, Any]:
    supervisor = _read_json_file(AUTO_TRADER_SUPERVISOR_STATUS_FILE)
    worker = _read_json_file(AUTO_TRADER_WORKER_RUNTIME_FILE)
    worker_pid = None
    raw_pid = worker.get("pid")
    if str(raw_pid).isdigit():
        worker_pid = int(raw_pid)
    if not worker_pid:
        worker_pid = read_pid_file(AUTO_TRADER_PID_FILE)
    worker_alive = is_pid_alive(worker_pid)
    if (not worker_alive) and (worker_pid is None):
        worker_alive = bool(worker.get("worker_running"))
    supervisor_alive = _is_auto_trader_supervisor_running()
    return {
        "supervisor": supervisor,
        "worker": worker,
        "worker_status": worker.get("status"),
        "worker_pid": worker_pid,
        "worker_running": worker_alive,
        "supervisor_running": supervisor_alive,
        "last_scan_summary": worker.get("last_scan_summary"),
        "scheduler": worker.get("scheduler"),
    }


def _start_auto_trader_worker() -> str:
    with _AUTO_TRADER_PROCESS_OP_LOCK:
        _cleanup_orphan_auto_trader_processes()
        running_supervisors = _list_python_pids_by_script("auto_trader_supervisor.py")
        if running_supervisors:
            # 若已存在 supervisor，则视为已运行，避免并发 start 造成双 supervisor。
            return "already_running"
        p = _managed_processes.get("auto_trader_supervisor")
        if p and p.poll() is None:
            return "already_running"
        pid = read_pid_file(AUTO_TRADER_SUPERVISOR_PID_FILE)
        if is_pid_alive(pid):
            if _is_auto_trader_script_pid(pid):
                return "already_running"
            # PID 复用保护：pid 文件可能陈旧，命中了其它进程（包括 backend）时不能误判为 supervisor 仍在运行。
            _remove_file_silent(AUTO_TRADER_SUPERVISOR_PID_FILE)
        _remove_file_silent(AUTO_TRADER_SUPERVISOR_STOP_FILE)
        env = os.environ.copy()
        env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        script = os.path.join(ROOT, "api", "auto_trader_supervisor.py")
        _managed_processes["auto_trader_supervisor"] = subprocess.Popen(  # noqa: S603
            [sys.executable, "-u", script],
            cwd=ROOT,
            env=env,
            **_win_subprocess_silent_kwargs(),
        )
        return "started"


def _stop_auto_trader_worker(timeout_seconds: float = 5.0) -> str:
    with _AUTO_TRADER_PROCESS_OP_LOCK:
        stopped_any = False
        _remove_file_silent(AUTO_TRADER_SUPERVISOR_STOP_FILE)
        try:
            with open(AUTO_TRADER_SUPERVISOR_STOP_FILE, "w", encoding="utf-8") as f:
                f.write(datetime.now().isoformat())
        except Exception:
            pass

        p = _managed_processes.get("auto_trader_supervisor")
        if p is not None:
            if p.poll() is None:
                try:
                    p.terminate()
                    p.wait(timeout=max(1.0, timeout_seconds))
                    stopped_any = True
                except Exception:
                    pass
            # 与飞书 Bot 一致：停止后丢弃句柄，避免误判；API 热重载后本就没有句柄，靠 pid 文件收尾
            _managed_processes.pop("auto_trader_supervisor", None)
        supervisor_status = _read_json_file(AUTO_TRADER_SUPERVISOR_STATUS_FILE)
        worker_runtime = _read_json_file(AUTO_TRADER_WORKER_RUNTIME_FILE)
        candidate_pids = {
            read_pid_file(AUTO_TRADER_SUPERVISOR_PID_FILE),
            read_pid_file(AUTO_TRADER_PID_FILE),
            int(supervisor_status.get("worker_pid")) if str(supervisor_status.get("worker_pid", "")).isdigit() else None,
            int(worker_runtime.get("pid")) if str(worker_runtime.get("pid", "")).isdigit() else None,
        }
        for pid in candidate_pids:
            # 仅允许终止 auto_trader 相关脚本进程，避免 PID 复用导致误杀 backend/其它服务。
            if _is_auto_trader_script_pid(pid):
                stopped_any = _terminate_pid(pid, timeout_seconds) or stopped_any
        if _cleanup_orphan_auto_trader_processes() > 0:
            stopped_any = True
        # 再做一次兜底清理，覆盖 supervisor 刚退出后短暂拉起子进程的竞态窗口。
        if _cleanup_orphan_auto_trader_processes() > 0:
            stopped_any = True
        if not is_pid_alive(read_pid_file(AUTO_TRADER_PID_FILE)):
            _remove_file_silent(AUTO_TRADER_WORKER_RUNTIME_FILE)
        if not is_pid_alive(read_pid_file(AUTO_TRADER_SUPERVISOR_PID_FILE)):
            _remove_file_silent(AUTO_TRADER_SUPERVISOR_STATUS_FILE)

        if stopped_any:
            return "stopped"
        return "not_running"


def _sync_auto_trader_worker_with_config(cfg: dict[str, Any]) -> dict[str, str]:
    """
    使独立 Worker/Supervisor 与 auto_trader_config.enabled 一致。
    此前仅「Setup 启动服务」会拉起进程，用户在自动交易页打开开关并保存配置时 Worker 不会启动，导致 runtime 长期不更新。
    """
    out: dict[str, str] = {}
    if bool(cfg.get("enabled")):
        auto_trader.stop_scheduler()
        if _is_auto_trader_supervisor_running():
            out["worker_supervisor"] = "already_running"
        else:
            out["worker_supervisor"] = str(_start_auto_trader_worker())
    else:
        auto_trader.stop_scheduler()
        out["worker_supervisor"] = str(_stop_auto_trader_worker())
    return out


def _wait_auto_trader_processes_stopped(timeout_seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        supervisor_alive = _is_auto_trader_supervisor_running()
        worker_pid = read_pid_file(AUTO_TRADER_PID_FILE)
        worker_alive = is_pid_alive(worker_pid)
        orphan_supervisors = _list_python_pids_by_script("auto_trader_supervisor.py")
        orphan_workers = _list_python_pids_by_script("auto_trader_worker.py")
        if (not supervisor_alive) and (not worker_alive) and (not orphan_supervisors) and (not orphan_workers):
            return True
        time.sleep(0.25)
    return False


def _resolve_period(kline: BacktestKline):
    """Map API kline string to LongPort Period enum."""
    candidates = {
        "1m": ["Min_1", "Min1", "OneMin"],
        "5m": ["Min_5", "Min5", "FiveMin"],
        "10m": ["Min_10", "Min10", "TenMin"],
        "30m": ["Min_30", "Min30", "ThirtyMin"],
        "1h": ["Min_60", "Hour", "H1", "Min60"],
        "2h": ["Min_120", "Min120", "TwoHour"],
        "4h": ["Min_240", "Min240", "FourHour"],
        "1d": ["Day", "D1"],
    }[kline]
    for name in candidates:
        p = getattr(Period, name, None)
        if p is not None:
            return p
    raise HTTPException(status_code=400, detail=f"不支持的K线周期: {kline}")


AGENT_POLICY_LOCKED_FIELDS = {
    "max_total_exposure",
    "min_cash_ratio",
    "max_position_value",
    "max_daily_trades",
}

# OpenClaw/agent optimization policy:
# - only strategy/timing/rhythm fields are writable by agent
# - account-level hard risk fields stay locked
AGENT_POLICY_FIELD_RULES: dict[str, dict[str, Any]] = {
    "entry_rule": {"type": "enum", "choices": ["strategy_cross", "breakout", "mean_reversion"]},
    "kline": {"type": "enum", "choices": ["1m", "5m", "10m", "30m", "1h", "2h", "4h", "1d"]},
    "signal_relaxed_mode": {"type": "bool"},
    "dry_run_mode": {"type": "bool"},
    "auto_execute": {"type": "bool"},
    "active_template": {"type": "enum", "choices": ["trend", "mean_reversion", "defensive", "custom"]},
    "breakout_lookback_bars": {"type": "range", "min": 10, "max": 60},
    "breakout_volume_ratio": {"type": "range", "min": 1.0, "max": 2.5},
    "mean_reversion_rsi_threshold": {"type": "range", "min": 20.0, "max": 45.0},
    "mean_reversion_deviation_pct": {"type": "range", "min": 0.8, "max": 6.0},
    "backtest_days": {"type": "range", "min": 60, "max": 240},
    "signal_bars_days": {"type": "range", "min": 45, "max": 180},
    "top_n": {"type": "range", "min": 3, "max": 20},
    "interval_seconds": {"type": "range", "min": 60, "max": 900},
    "same_direction_max_new_orders_per_scan": {"type": "range", "min": 1, "max": 3},
    "max_concurrent_long_positions": {"type": "range", "min": 3, "max": 12},
    "ml_filter_enabled": {"type": "bool"},
    "ml_model_type": {"type": "enum", "choices": ["logreg", "random_forest", "gbdt"]},
    "ml_threshold": {"type": "range", "min": 0.5, "max": 0.95},
    "ml_horizon_days": {"type": "range", "min": 1, "max": 30},
    "ml_train_ratio": {"type": "range", "min": 0.5, "max": 0.9},
    "ml_walk_forward_windows": {"type": "range", "min": 1, "max": 10},
    "ml_filter_cache_minutes": {"type": "range", "min": 0, "max": 240},
    "research_allocation_enabled": {"type": "bool"},
    "research_allocation_max_age_minutes": {"type": "range", "min": 0, "max": 10080},
    "research_allocation_notional_scale": {"type": "range", "min": 0.01, "max": 3.0},
    "same_symbol_cooldown_minutes": {"type": "range", "min": 10, "max": 180},
    "same_symbol_max_trades_per_day": {"type": "range", "min": 1, "max": 3},
    "same_symbol_max_sells_per_day": {"type": "range", "min": 1, "max": 3},
    "order_quantity": {"type": "range", "min": 50, "max": 300},
    "hard_stop_pct": {"type": "range", "min": 2.0, "max": 10.0},
    "take_profit_pct": {"type": "range", "min": 4.0, "max": 25.0},
    "time_stop_hours": {"type": "range", "min": 12, "max": 168},
    "cost_model": {
        "type": "object",
        "fields": {
            "commission_bps": {"type": "range", "min": 0.0, "max": 15.0},
            "slippage_bps": {"type": "range", "min": 0.0, "max": 20.0},
        },
    },
}


def _validate_agent_policy_update(
    updates: dict[str, Any],
    current_cfg: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    accepted: dict[str, Any] = {}
    violations: list[dict[str, Any]] = []

    for key, val in updates.items():
        if key in AGENT_POLICY_LOCKED_FIELDS:
            current_val = current_cfg.get(key)
            if val != current_val:
                violations.append(
                    {
                        "field": key,
                        "reason": "locked_field",
                        "current": current_val,
                        "attempted": val,
                    }
                )
            continue

        rule = AGENT_POLICY_FIELD_RULES.get(key)
        if not rule:
            violations.append({"field": key, "reason": "field_not_whitelisted", "attempted": val})
            continue

        rtype = str(rule.get("type"))
        if rtype == "bool":
            if isinstance(val, bool):
                accepted[key] = val
            else:
                violations.append({"field": key, "reason": "invalid_type_bool", "attempted": val})
            continue

        if rtype == "enum":
            choices = list(rule.get("choices", []))
            if val in choices:
                accepted[key] = val
            else:
                violations.append(
                    {"field": key, "reason": "enum_out_of_range", "choices": choices, "attempted": val}
                )
            continue

        if rtype == "range":
            lo = float(rule["min"])
            hi = float(rule["max"])
            try:
                num = float(val)
            except Exception:
                violations.append({"field": key, "reason": "invalid_numeric", "attempted": val})
                continue
            if num < lo or num > hi:
                violations.append(
                    {"field": key, "reason": "out_of_range", "min": lo, "max": hi, "attempted": num}
                )
                continue
            # keep integer fields as int
            if isinstance(current_cfg.get(key), int):
                accepted[key] = int(round(num))
            else:
                accepted[key] = num
            continue

        if rtype == "object":
            if not isinstance(val, dict):
                violations.append({"field": key, "reason": "invalid_object", "attempted": val})
                continue
            sub_rules = dict(rule.get("fields", {}))
            current_obj = current_cfg.get(key) if isinstance(current_cfg.get(key), dict) else {}
            next_obj = dict(current_obj or {})
            for sk, sv in val.items():
                sr = sub_rules.get(sk)
                if not sr:
                    violations.append(
                        {"field": f"{key}.{sk}", "reason": "field_not_whitelisted", "attempted": sv}
                    )
                    continue
                slo = float(sr["min"])
                shi = float(sr["max"])
                try:
                    snum = float(sv)
                except Exception:
                    violations.append({"field": f"{key}.{sk}", "reason": "invalid_numeric", "attempted": sv})
                    continue
                if snum < slo or snum > shi:
                    violations.append(
                        {
                            "field": f"{key}.{sk}",
                            "reason": "out_of_range",
                            "min": slo,
                            "max": shi,
                            "attempted": snum,
                        }
                    )
                    continue
                next_obj[sk] = snum
            accepted[key] = next_obj
            continue

        violations.append({"field": key, "reason": "unsupported_rule_type", "attempted": val})

    return accepted, violations


ENV_FILE = os.path.join(ROOT, ".env")
FEE_SCHEDULE_FILE = os.path.join(ROOT, "config", "fee_schedule.json")
ENV_VAR_MAP = {
    "longport_app_key": "LONGPORT_APP_KEY",
    "longport_app_secret": "LONGPORT_APP_SECRET",
    "longport_access_token": "LONGPORT_ACCESS_TOKEN",
    "feishu_app_id": "FEISHU_APP_ID",
    "feishu_app_secret": "FEISHU_APP_SECRET",
    "feishu_scheduled_chat_id": "FEISHU_SCHEDULED_CHAT_ID",
    "finnhub_api_key": "FINNHUB_API_KEY",
    "tiingo_api_key": "TIINGO_API_KEY",
    "fred_api_key": "FRED_API_KEY",
    "coingecko_api_key": "COINGECKO_API_KEY",
    "openclaw_mcp_max_level": "OPENCLAW_MCP_MAX_LEVEL",
    "openclaw_mcp_allow_l3": "OPENCLAW_MCP_ALLOW_L3",
    "openclaw_mcp_l3_confirmation_token": "OPENCLAW_MCP_L3_CONFIRMATION_TOKEN",
    "openbb_enabled": "OPENBB_ENABLED",
    "openbb_base_url": "OPENBB_BASE_URL",
    "openbb_timeout_seconds": "OPENBB_TIMEOUT_SECONDS",
}

_LEVEL_RANK = {"L1": 1, "L2": 2, "L3": 3}


def _normalize_level(raw: str | None, default: str = "L2") -> str:
    x = str(raw or "").strip().upper()
    return x if x in _LEVEL_RANK else default


def _env_bool(name: str, default: bool = False) -> bool:
    val = str(os.getenv(name, "")).strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "on"}


def _ensure_l3_confirmation(token: str | None) -> None:
    max_level = _normalize_level(os.getenv("OPENCLAW_MCP_MAX_LEVEL", "L2"), default="L2")
    allow_l3 = _env_bool("OPENCLAW_MCP_ALLOW_L3", default=False)
    expected = str(os.getenv("OPENCLAW_MCP_L3_CONFIRMATION_TOKEN", "")).strip()
    if _LEVEL_RANK.get(max_level, 2) < 3:
        raise HTTPException(status_code=403, detail="当前权限不足：需要 L3")
    if not allow_l3:
        raise HTTPException(status_code=403, detail="L3敏感交易已禁用")
    if expected and str(token or "").strip() != expected:
        raise HTTPException(status_code=403, detail="confirmation_token 无效或缺失")


def _load_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _save_env_file(path: str, data: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in sorted(data.items())]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _mask_secret(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "*" * len(v)
    return v[:4] + "*" * (len(v) - 8) + v[-4:]


def _load_fee_schedule_file(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_fee_schedule_file(path: str, schedule: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _init_fee_schedule_runtime() -> None:
    current = _load_fee_schedule_file(FEE_SCHEDULE_FILE)
    if current:
        set_fee_schedule(current)
    else:
        _save_fee_schedule_file(FEE_SCHEDULE_FILE, get_fee_schedule())


def _kline_to_minutes(kline: BacktestKline) -> int:
    """将K线周期转换为分钟数"""
    return {"1m": 1, "5m": 5, "10m": 10, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 60 * 24}.get(
        kline, 60 * 24
    )


# 单次按日历日拉 K 线的上限（原 365 会导致日 K 1000 根窗口不足）
_MAX_CALENDAR_DAYS_SINGLE_FETCH = 3650


def _is_invalid_symbol_error(err: Exception | str) -> bool:
    text = str(err or "").lower()
    return ("invalid symbol" in text) or ("code=301600" in text)


def _is_longport_connect_error(err: Exception | str) -> bool:
    return is_longport_connect_error(err)


def _mark_invalid_symbol(symbol: str, reason: str = "") -> None:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return
    with _LONGPORT_INVALID_SYMBOL_CACHE_LOCK:
        _LONGPORT_INVALID_SYMBOL_CACHE[sym] = (time.time(), str(reason or "invalid_symbol"))


def _is_symbol_marked_invalid(symbol: str) -> bool:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return True
    now = time.time()
    with _LONGPORT_INVALID_SYMBOL_CACHE_LOCK:
        item = _LONGPORT_INVALID_SYMBOL_CACHE.get(sym)
        if not item:
            return False
        ts, _reason = item
        if (now - float(ts)) > float(_LONGPORT_INVALID_SYMBOL_CACHE_TTL_SECONDS):
            _LONGPORT_INVALID_SYMBOL_CACHE.pop(sym, None)
            return False
        return True


def _longport_bars_mem_cache_key(mode: str, symbol: str, days: int, kline: str) -> str:
    return f"{str(mode).strip().lower()}::{str(symbol).strip().upper()}::{int(days)}::{str(kline).strip().lower()}"


def _longport_bars_mem_cache_get(key: str) -> list[Bar] | None:
    now = time.time()
    with _LONGPORT_BARS_MEM_CACHE_LOCK:
        row = _LONGPORT_BARS_MEM_CACHE.get(key)
        if not isinstance(row, tuple) or len(row) != 2:
            return None
        ts, bars = row
        if (now - float(ts)) > float(_LONGPORT_BARS_MEM_CACHE_TTL_SECONDS):
            _LONGPORT_BARS_MEM_CACHE.pop(key, None)
            return None
        if not isinstance(bars, list) or not bars:
            return [] if isinstance(bars, list) else None
        return list(bars)


def _longport_bars_mem_cache_put(key: str, bars: list[Bar]) -> None:
    if not isinstance(bars, list):
        return
    with _LONGPORT_BARS_MEM_CACHE_LOCK:
        _LONGPORT_BARS_MEM_CACHE[key] = (time.time(), list(bars))
        if len(_LONGPORT_BARS_MEM_CACHE) <= _LONGPORT_BARS_MEM_CACHE_MAX_ENTRIES:
            return
        overflow = len(_LONGPORT_BARS_MEM_CACHE) - _LONGPORT_BARS_MEM_CACHE_MAX_ENTRIES
        if overflow <= 0:
            return
        oldest = sorted(_LONGPORT_BARS_MEM_CACHE.items(), key=lambda item: float(item[1][0]))
        for victim_key, _ in oldest[:overflow]:
            _LONGPORT_BARS_MEM_CACHE.pop(victim_key, None)


def _longport_bars_inflight_enter(key: str) -> tuple[threading.Event, bool]:
    now = time.time()
    with _LONGPORT_BARS_INFLIGHT_LOCK:
        stale_keys = [
            k
            for k, row in _LONGPORT_BARS_INFLIGHT.items()
            if isinstance(row, dict)
            and bool(row.get("done"))
            and (now - float(row.get("ts", 0.0) or 0.0)) > float(_LONGPORT_BARS_INFLIGHT_HOLD_SECONDS)
        ]
        for stale in stale_keys:
            _LONGPORT_BARS_INFLIGHT.pop(stale, None)
        row = _LONGPORT_BARS_INFLIGHT.get(key)
        if isinstance(row, dict):
            ev = row.get("event")
            if isinstance(ev, threading.Event):
                return ev, False
        ev = threading.Event()
        _LONGPORT_BARS_INFLIGHT[key] = {
            "event": ev,
            "done": False,
            "bars": None,
            "error": None,
            "ts": now,
        }
        return ev, True


def _longport_bars_inflight_resolve(key: str, bars: list[Bar] | None = None, error: str | None = None) -> None:
    with _LONGPORT_BARS_INFLIGHT_LOCK:
        row = _LONGPORT_BARS_INFLIGHT.get(key)
        if not isinstance(row, dict):
            return
        ev = row.get("event")
        if not isinstance(ev, threading.Event):
            return
        row["done"] = True
        row["bars"] = list(bars or [])
        row["error"] = str(error or "")
        row["ts"] = time.time()
        ev.set()


def _longport_bars_inflight_await(
    key: str, ev: threading.Event, timeout_seconds: float = 25.0
) -> tuple[list[Bar] | None, str | None]:
    ok = ev.wait(timeout=max(1.0, float(timeout_seconds)))
    if not ok:
        return None, None
    with _LONGPORT_BARS_INFLIGHT_LOCK:
        row = _LONGPORT_BARS_INFLIGHT.get(key)
        if not isinstance(row, dict) or not bool(row.get("done")):
            return None, None
        err = str(row.get("error", "") or "").strip()
        bars = row.get("bars")
        if not isinstance(bars, list):
            bars = []
        return list(bars), (err or None)


def _history_json_items_to_bars(items: list[Any]) -> list[Bar]:
    """将网关或 HTTP 返回的 history-bars items 转为 Bar 列表。"""
    out: list[Bar] = []
    for x in items:
        if not isinstance(x, dict):
            continue
        try:
            out.append(
                Bar(
                    date=coerce_bar_datetime(x.get("date")),
                    open=float(x.get("open", 0.0) or 0.0),
                    high=float(x.get("high", 0.0) or 0.0),
                    low=float(x.get("low", 0.0) or 0.0),
                    close=float(x.get("close", 0.0) or 0.0),
                    volume=float(x.get("volume", 0.0) or 0.0),
                )
            )
        except Exception:
            continue
    return out


def _fetch_bars(symbol: str, days: int, kline: BacktestKline = "1d") -> list[Bar]:
    """获取K线数据（单次请求，最多1000根）"""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return []
    cache_key = _longport_bars_mem_cache_key("single", sym, int(days), str(kline))
    cached = _longport_bars_mem_cache_get(cache_key)
    if cached is not None:
        return cached
    if _is_symbol_marked_invalid(sym):
        return []
    inflight_ev, inflight_owner = _longport_bars_inflight_enter(cache_key)
    if not inflight_owner:
        bars, err = _longport_bars_inflight_await(cache_key, inflight_ev, timeout_seconds=25.0)
        if bars is not None:
            if bars:
                _longport_bars_mem_cache_put(cache_key, bars)
            if err:
                raise RuntimeError(err)
            return bars
    try:
        gw = _gateway_get_json(
            "/internal/longport/history-bars",
            {"symbol": sym, "days": int(days), "kline": str(kline)},
            timeout=max(LONGPORT_GATEWAY_TIMEOUT_SECONDS, 20.0),
        )
        items = gw.get("items") if isinstance(gw, dict) else None
        if isinstance(items, list) and items:
            out = _history_json_items_to_bars(items)
            if out:
                _longport_bars_mem_cache_put(cache_key, out)
                if inflight_owner:
                    _longport_bars_inflight_resolve(cache_key, bars=out)
                return out

        acquired = acquire_history_slot(timeout=20.0)
        if not acquired:
            raise RuntimeError("longport_history_queue_busy")
        try:
            try:
                qctx, _ = ensure_contexts()
            except Exception as e:
                if _is_longport_connect_error(e):
                    throttled_reset_contexts(reset_contexts, _RUNTIME_STATE)
                    if inflight_owner:
                        _longport_bars_inflight_resolve(cache_key, bars=[])
                    return []
                raise
            end_date = date.today()
            start_date = end_date - timedelta(days=days)
            period = _resolve_period(kline)
            try:
                candles = qctx.history_candlesticks_by_date(
                    symbol=sym,
                    period=period,
                    adjust_type=AdjustType.ForwardAdjust,
                    start=start_date,
                    end=end_date,
                    trade_sessions=TradeSessions.All,
                )
            except Exception as e:
                if _is_invalid_symbol_error(e):
                    _mark_invalid_symbol(sym, str(e))
                    if inflight_owner:
                        _longport_bars_inflight_resolve(cache_key, bars=[])
                    return []
                raise
        finally:
            release_history_slot()
        out = [
            Bar(
                date=coerce_bar_datetime(c.timestamp),
                open=float(c.open),
                high=float(c.high),
                low=float(c.low),
                close=float(c.close),
                volume=float(c.volume),
            )
            for c in candles
        ]
        _longport_bars_mem_cache_put(cache_key, out)
        if inflight_owner:
            _longport_bars_inflight_resolve(cache_key, bars=out)
        return out
    except Exception as e:
        if inflight_owner:
            _longport_bars_inflight_resolve(cache_key, error=str(e))
        raise


def _fetch_bars_paginated(symbol: str, periods: int, kline: BacktestKline = "1d") -> list[Bar]:
    """分页获取K线数据，支持超过1000根
    
    通过多次请求获取历史数据，每次请求1000根，然后合并结果。
    使用日期范围分页：先获取最近的数据，然后根据最早日期继续往前获取。
    """
    import logging
    logger = logging.getLogger(__name__)
    
    sym = str(symbol or "").strip().upper()
    if not sym:
        return []
    if _is_symbol_marked_invalid(sym):
        return []

    MAX_CANDLES_PER_REQUEST = 1000

    try:
        qctx, _ = ensure_contexts()
    except Exception as e:
        if _is_longport_connect_error(e):
            logger.warning("分页获取K线上下文初始化失败: symbol=%s error=%s", sym, e)
            throttled_reset_contexts(reset_contexts, _RUNTIME_STATE)
            return []
        raise
    period = _resolve_period(kline)
    all_bars: list[Bar] = []
    
    # 计算每次请求需要覆盖的天数（估算）
    minutes_per_candle = _kline_to_minutes(kline)
    candles_per_request = min(MAX_CANDLES_PER_REQUEST, periods)
    days_per_request = int((candles_per_request * minutes_per_candle / (60 * 24)) * 1.5) + 1
    days_per_request = max(1, days_per_request)  # 至少1天
    
    current_end_date = date.today()
    remaining_periods = periods
    empty_windows = 0
    max_empty_windows = 3
    no_progress_windows = 0
    max_no_progress_windows = 3
    
    while remaining_periods > 0 and len(all_bars) < periods:
        current_start_date = current_end_date - timedelta(days=days_per_request)
        
        try:
            acquired = acquire_history_slot(timeout=20.0)
            if not acquired:
                logger.warning("longport_history_queue_busy: symbol=%s", symbol)
                break
            try:
                candles = qctx.history_candlesticks_by_date(
                    symbol=sym,
                    period=period,
                    adjust_type=AdjustType.ForwardAdjust,
                    start=current_start_date,
                    end=current_end_date,
                    trade_sessions=TradeSessions.All,
                )
            finally:
                release_history_slot()
            
            if not candles:
                empty_windows += 1
                logger.info(
                    "分页获取K线空窗口: symbol=%s window=%s~%s (%s/%s)",
                    sym,
                    current_start_date,
                    current_end_date,
                    empty_windows,
                    max_empty_windows,
                )
                current_end_date = current_start_date - timedelta(days=1)
                days_per_request = min(_MAX_CALENDAR_DAYS_SINGLE_FETCH, int(days_per_request * 1.5) + 1)
                if empty_windows >= max_empty_windows:
                    break
                continue
            empty_windows = 0
            
            # 转换为 Bar（保留完整时间戳，分钟/小时 K 同日多根不合并）
            bars = [
                Bar(
                    date=coerce_bar_datetime(c.timestamp),
                    open=float(c.open),
                    high=float(c.high),
                    low=float(c.low),
                    close=float(c.close),
                    volume=float(c.volume),
                )
                for c in candles
            ]
            
            # 去重并合并（按完整 bar 时间，避免仅用日历日）
            existing_ts = {b.date for b in all_bars}
            new_bars = [b for b in bars if b.date not in existing_ts]
            for b in new_bars:
                existing_ts.add(b.date)
            all_bars.extend(new_bars)
            
            all_bars.sort(key=lambda x: x.date)
            
            logger.info(f"分页获取: symbol={sym} 已获取 {len(all_bars)}/{periods} 根K线, 本次 {len(new_bars)} 根")
            
            # 更新下一次请求的范围（往前推）
            if bars:
                earliest_cal = min(b.date for b in bars).date()
                current_end_date = earliest_cal - timedelta(days=1)
            else:
                break
            
            remaining_periods = periods - len(all_bars)

            # 小周期（如 4h）在自然日窗口中本就可能 <500 根，不能据此提前停止。
            # 仅在连续无新增时停止，避免误判截断同时防止异常循环。
            if len(new_bars) == 0 and remaining_periods > 0:
                no_progress_windows += 1
                logger.info(
                    "分页获取K线无新增: symbol=%s (%s/%s)",
                    sym,
                    no_progress_windows,
                    max_no_progress_windows,
                )
                if no_progress_windows >= max_no_progress_windows:
                    break
            else:
                no_progress_windows = 0
                
        except Exception as e:
            if _is_invalid_symbol_error(e):
                _mark_invalid_symbol(sym, str(e))
                logger.warning("分页获取K线跳过无效标的: symbol=%s error=%s", sym, e)
                return []
            logger.warning("分页获取K线数据失败: symbol=%s error=%s", sym, e)
            break
    
    # 只返回最近的 periods 根
    if len(all_bars) > periods:
        all_bars = all_bars[-periods:]
    
    logger.info(f"分页获取完成: symbol={sym} 共 {len(all_bars)} 根K线")
    return all_bars


def _fetch_bars_by_periods(symbol: str, periods: int, kline: BacktestKline = "1d") -> list[Bar]:
    """根据周期数获取K线数据
    
    如果周期数 <= 1000，使用单次请求
    如果周期数 > 1000，使用分页请求
    """
    MAX_CANDLES_PER_REQUEST = 1000
    sym = str(symbol or "").strip().upper()
    if not sym:
        return []
    if _is_symbol_marked_invalid(sym):
        return []
    
    if periods <= MAX_CANDLES_PER_REQUEST:
        minutes_per_candle = _kline_to_minutes(kline)
        total_minutes = periods * minutes_per_candle
        days_needed = int((total_minutes / (60 * 24)) * 1.2) + 1
        days_needed = max(30, min(_MAX_CALENDAR_DAYS_SINGLE_FETCH, days_needed))

        bars = _fetch_bars(sym, days_needed, kline)
        if len(bars) > periods:
            bars = bars[-periods:]
        # 单次窗口仍不足（缓存/API 截断等）时自动走分页补齐
        if len(bars) < periods:
            return _fetch_bars_paginated(sym, periods, kline)
        return bars
    else:
        # 需要分页获取
        return _fetch_bars_paginated(sym, periods, kline)


def _build_ml_probability_map(
    bars: list[Bar],
    model_type: str = "logreg",
    horizon_days: int = 5,
    transaction_cost_bps: float = 0.0,
    train_ratio: float = 0.7,
    walk_forward_windows: int = 4,
) -> tuple[dict[str, float], dict[str, Any]]:
    """基于历史K线训练分类器，返回 date -> up_probability 与 walk-forward 摘要。"""
    if len(bars) < 100:
        return {}, {"enabled": True, "reason": "insufficient_bars", "bars": len(bars)}
    df = build_ml_feature_frame(
        bars,
        horizon_days=horizon_days,
        transaction_cost_bps=transaction_cost_bps,
    )
    if df is None:
        return {}, {"enabled": True, "reason": "feature_frame_unavailable"}
    if len(df) < 80:
        return {}, {"enabled": True, "reason": "insufficient_samples", "samples": len(df)}

    feature_cols = FEATURE_COLUMNS
    X = df[feature_cols].astype(float).values
    y = df["label"].astype(int).values

    if len(set(y.tolist())) < 2:
        return {}, {"enabled": True, "reason": "single_class_labels", "samples": len(df)}

    wf_probs, wf_summary = walk_forward_probability_map(
        df=df,
        model_type=model_type,
        train_ratio=train_ratio,
        min_train_size=60,
        test_window=20,
        max_windows=walk_forward_windows,
    )

    model = create_ml_classifier(model_type)
    model.fit(X, y)
    probs = model.predict_proba(X)[:, 1]
    base_map = {str(df.iloc[i]["date"]): float(probs[i]) for i in range(len(df))}
    base_map.update(wf_probs)
    return base_map, wf_summary


_ET = ZoneInfo("America/New_York")
_QUOTE_TS_SOURCE_TZ = ZoneInfo(os.getenv("QUOTE_TS_SOURCE_TZ", "Asia/Shanghai"))


def _as_et_datetime(raw: Any) -> Optional[datetime]:
    """Convert various timestamp forms to America/New_York datetime."""
    if raw is None:
        return None
    dt: Optional[datetime] = None
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
        # LongPort often returns naive timestamps in broker-side local timezone (commonly UTC+8).
        dt = dt.replace(tzinfo=_QUOTE_TS_SOURCE_TZ)
    return dt.astimezone(_ET)


def _extract_quote_timestamp(quote_obj: Any) -> Optional[datetime]:
    for attr in ("timestamp", "trade_timestamp", "updated_at", "time"):
        if hasattr(quote_obj, attr):
            dt = _as_et_datetime(getattr(quote_obj, attr))
            if dt is not None:
                return dt
    return None


def _is_fresh_for_session(kind: str, quote_ts_et: Optional[datetime], now_et: datetime) -> bool:
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


def _session_kind_et(now_et: datetime) -> str:
    t = now_et.timetz().replace(tzinfo=None)
    if dt_time(4, 0) <= t < dt_time(9, 30):
        return "盘前"
    if dt_time(9, 30) <= t < dt_time(16, 0):
        return "盘中"
    if dt_time(16, 0) <= t < dt_time(20, 0):
        return "盘后"
    return "夜盘"


def _is_us_symbol(symbol: str) -> bool:
    return str(symbol or "").strip().upper().endswith(".US")


def _assert_us_order_session_allowed(symbol: str) -> None:
    """US orders are allowed in pre/regular/post market, but blocked overnight."""
    if not _is_us_symbol(symbol):
        return
    now_et = datetime.now(timezone.utc).astimezone(_ET)
    session = _session_kind_et(now_et)
    if session == "夜盘":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "us_overnight_order_blocked",
                "message": "美股夜盘时段禁止提交订单，仅允许盘前/盘中/盘后下单。",
                "symbol": symbol,
                "session_et": session,
                "now_et": now_et.isoformat(),
            },
        )


def _get_realtime_price(q) -> tuple[float, str]:
    """按美东时段优先取价，并校验时间戳新鲜度。"""
    now_et = datetime.now(timezone.utc).astimezone(_ET)
    session = _session_kind_et(now_et)

    candidates: dict[str, Any] = {
        "盘前": getattr(q, "pre_market_quote", None),
        "盘后": getattr(q, "post_market_quote", None),
        "夜盘": getattr(q, "overnight_quote", None),
        "盘中": q,
    }

    preferred_order = {
        "盘前": ["盘前", "盘中", "夜盘", "盘后"],
        "盘中": ["盘中", "盘前", "盘后", "夜盘"],
        "盘后": ["盘后", "盘中", "夜盘", "盘前"],
        "夜盘": ["夜盘", "盘后", "盘中", "盘前"],
    }[session]

    # First pass: strictly require timestamp freshness for session-specific quotes.
    for kind in preferred_order:
        obj = candidates.get(kind)
        if not obj or not getattr(obj, "last_done", None):
            continue
        if kind == "盘中":
            return float(obj.last_done), kind
        ts = _extract_quote_timestamp(obj)
        if _is_fresh_for_session(kind, ts, now_et):
            return float(obj.last_done), kind

    # Second pass: fallback to any available price to avoid returning zero.
    for kind in preferred_order:
        obj = candidates.get(kind)
        if obj and getattr(obj, "last_done", None):
            return float(obj.last_done), kind

    return float(q.last_done), "盘中"


def _quote_last(symbol: str) -> Optional[dict[str, float]]:
    """获取最新实时价格（支持盘前盘后夜盘）"""
    gw = _gateway_get_json("/internal/longport/quote", {"symbol": str(symbol).strip().upper()})
    if isinstance(gw, dict) and bool(gw.get("available")):
        return {
            "last": float(gw.get("last", 0.0) or 0.0),
            "change_pct": round(float(gw.get("change_pct", 0.0) or 0.0), 2),
            "price_type": str(gw.get("price_type", "")),
            "prev_close": float(gw.get("prev_close", 0.0) or 0.0),
        }

    qctx, _ = ensure_contexts()
    try:
        qs = qctx.quote([symbol])
        if not qs:
            return None
        q = qs[0]
        
        # 获取实时价格（优先盘前盘后）
        last, price_type = _get_realtime_price(q)
        prev = float(q.prev_close)
        chg = ((last - prev) / prev * 100) if prev else 0.0
        
        return {
            "last": last, 
            "change_pct": round(chg, 2),
            "price_type": price_type,
            "prev_close": prev
        }
    except Exception:
        return None


def _execute_trade_for_auto_trader(action: str, symbol: str, quantity: int, price: float) -> dict[str, Any]:
    """为AutoTrader执行交易"""
    try:
        from api import runtime_bridge as rt

        body = SubmitOrderBody(
            action="buy" if str(action).lower() != "sell" else "sell",
            symbol=symbol,
            quantity=quantity,
            price=price,
        )
        return rt.trade_submit_order(body.model_dump())
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_positions_for_auto_trader() -> dict[str, Any]:
    """为AutoTrader获取持仓"""
    from api import runtime_bridge as rt

    return rt.trade_positions()


def _get_account_for_auto_trader() -> dict[str, Any]:
    """为AutoTrader获取账户信息"""
    from api import runtime_bridge as rt

    return rt.trade_account()


auto_trader = AutoTraderService(
    fetch_bars=lambda symbol, days, kline: _fetch_bars_calendar_days(symbol, days, kline),  # type: ignore[arg-type]
    quote_last=_quote_last,
    send_feishu=make_feishu_sender(os.path.join(MCP_DIR, "notification_config.json")),
    execute_trade=_execute_trade_for_auto_trader,
    get_positions=_get_positions_for_auto_trader,
    get_account=_get_account_for_auto_trader,
    config_path=os.path.join(ROOT, "api", "auto_trader_config.json"),
)
_init_fee_schedule_runtime()

_feishu_broadcast_sender = make_feishu_sender(os.path.join(MCP_DIR, "notification_config.json"))
NOTIFICATION_REVERSAL_STATE_FILE = os.path.join(ROOT, ".notification_bottom_reversal_state.json")
_reversal_watch_lock = threading.Lock()


def _load_reversal_watch_state() -> dict[str, Any]:
    try:
        if not os.path.exists(NOTIFICATION_REVERSAL_STATE_FILE):
            return {}
        with open(NOTIFICATION_REVERSAL_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        sym = data.get("symbols")
        return sym if isinstance(sym, dict) else {}
    except Exception:
        return {}


def _save_reversal_watch_state(symbols_state: dict[str, Any]) -> None:
    try:
        payload = {"symbols": symbols_state, "updated_at": datetime.now().isoformat()}
        tmp = NOTIFICATION_REVERSAL_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, NOTIFICATION_REVERSAL_STATE_FILE)
    except Exception:
        pass


def _reversal_watch_background_loop() -> None:
    """与信号中心同源 bottom_reversal_hint；按 notification_preferences 轮询并发飞书。"""
    from api.signal_center_signals import analyze_signal_center_from_closes

    time.sleep(15)
    while True:
        try:
            prefs = load_notification_preferences()
            br = prefs.get("bottom_reversal_watch")
            if not isinstance(br, dict) or not bool(br.get("enabled")):
                time.sleep(30)
                continue
            symbols = [str(s).strip().upper() for s in (br.get("symbols") or []) if str(s).strip()]
            interval = max(60, int(br.get("poll_interval_seconds") or 300))
            only_edge = bool(br.get("only_on_edge", True))
            cooldown_min = max(0, int(br.get("cooldown_minutes") or 0))
            if not symbols:
                time.sleep(min(interval, 60))
                continue

            with _reversal_watch_lock:
                st = _load_reversal_watch_state()

            now = datetime.now()
            for sym in symbols:
                try:
                    bars = _fetch_bars_calendar_days(sym, 90)
                    if len(bars) < 25:
                        continue
                    closes = [float(b.close) for b in bars]
                    snap = analyze_signal_center_from_closes(closes)
                    if not snap:
                        continue
                    hint = bool(snap["signals"].get("bottom_reversal_hint"))
                    row = st.get(sym)
                    if not isinstance(row, dict):
                        row = {}
                    prev_hint = row.get("last_hint")
                    prev_hint = bool(prev_hint) if prev_hint is not None else None

                    should_notify = False
                    if only_edge:
                        should_notify = hint and prev_hint is not True
                    else:
                        should_notify = hint

                    last_sent_s = row.get("last_sent_iso")
                    if should_notify and cooldown_min > 0 and last_sent_s:
                        try:
                            last_dt = datetime.fromisoformat(str(last_sent_s))
                            if (now - last_dt).total_seconds() < cooldown_min * 60:
                                should_notify = False
                        except Exception:
                            pass

                    row["last_hint"] = hint
                    if should_notify:
                        rsi_v = snap.get("rsi14", "-")
                        text = (
                            "[信号中心·底部反转提示]\n"
                            f"标的: {sym}\n"
                            f"时间: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"RSI14≈{rsi_v} | MA5={snap.get('ma5')} | MA20={snap.get('ma20')}\n"
                            "说明: 与页面「信号中心」底部反转提示同源（非投资建议）。"
                        )
                        try:
                            if _feishu_broadcast_sender(text):
                                row["last_sent_iso"] = now.isoformat()
                        except Exception:
                            pass
                    st[sym] = row
                except Exception:
                    continue

            with _reversal_watch_lock:
                _save_reversal_watch_state(st)

            time.sleep(interval)
        except Exception:
            time.sleep(60)


@app.on_event("startup")
def _startup_notification_reversal_watcher() -> None:
    if str(os.getenv("NOTIFICATION_API_REVERSAL_WATCH", "true")).strip().lower() not in {"1", "true", "yes", "on"}:
        return
    if not _is_main_process_runtime():
        return
    global _STARTUP_REVERSAL_WATCHER_STARTED
    with _STARTUP_SIDE_EFFECTS_LOCK:
        if _STARTUP_REVERSAL_WATCHER_STARTED:
            return
        _STARTUP_REVERSAL_WATCHER_STARTED = True
    try:
        t = threading.Thread(target=_reversal_watch_background_loop, name="notification-reversal-watch", daemon=True)
        t.start()
    except Exception:
        with _STARTUP_SIDE_EFFECTS_LOCK:
            _STARTUP_REVERSAL_WATCHER_STARTED = False


@app.on_event("startup")
def _startup_autostart_auto_trader_worker() -> None:
    """API 重启后子进程会退出：若配置仍启用自动交易，则重新拉起 Supervisor。"""
    if str(os.getenv("AUTO_TRADER_AUTOSTART_ON_API_BOOT", "true")).strip().lower() not in {"1", "true", "yes", "on"}:
        return
    if not _is_main_process_runtime():
        return
    global _STARTUP_AUTO_TRADER_BOOTSTRAPPED
    with _STARTUP_SIDE_EFFECTS_LOCK:
        if _STARTUP_AUTO_TRADER_BOOTSTRAPPED:
            return
        _STARTUP_AUTO_TRADER_BOOTSTRAPPED = True
    try:
        if _is_auto_trader_supervisor_running():
            return
        cfg = auto_trader.get_config()
        if bool(cfg.get("enabled")):
            _sync_auto_trader_worker_with_config(cfg)
    except Exception:
        with _STARTUP_SIDE_EFFECTS_LOCK:
            _STARTUP_AUTO_TRADER_BOOTSTRAPPED = False


def _market_snap(symbols: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """获取市场快照（支持盘前盘后实时价格）"""
    qctx, _ = ensure_contexts()
    raw = qctx.quote([s for s, _ in symbols])
    out: list[dict[str, Any]] = []
    for idx, (sym, name) in enumerate(symbols):
        if idx >= len(raw):
            continue
        q = raw[idx]
        
        # 使用实时价格（优先盘前盘后）
        last, price_type = _get_realtime_price(q)
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
        
        out.append(
            {
                "symbol": sym,
                "name": name,
                "last": last,
                "prev_close": prev,
                "change_pct": round(chg, 2),
                "high": high,
                "low": low,
                "price_type": price_type,
            }
        )
    return out


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "longport-ui-api"}


def _parse_client_bars_for_backtest(items: list[BacktestBarItem]) -> list[Bar]:
    out: list[Bar] = []
    for x in items:
        try:
            out.append(
                Bar(
                    date=coerce_bar_datetime(x.date),
                    open=float(x.open),
                    high=float(x.high),
                    low=float(x.low),
                    close=float(x.close),
                    volume=float(x.volume),
                )
            )
        except Exception:
            continue
    out.sort(key=lambda b: b.date)
    return out


def _safe_kline_cache_stem(symbol: str) -> str:
    s = str(symbol or "").strip().upper().replace(".", "_").replace("-", "_")
    if not s or len(s) > 64 or not all(c.isalnum() or c == "_" for c in s):
        raise HTTPException(status_code=400, detail="invalid_symbol_for_cache")
    return s


def _kline_server_cache_filename(symbol: str, kline: str, periods: int, days: int) -> str:
    stem = _safe_kline_cache_stem(symbol)
    kl = str(kline or "1d").strip().lower()
    if periods and int(periods) > 0:
        return f"{stem}__{kl}__p{int(periods)}.json"
    return f"{stem}__{kl}__d{max(1, int(days))}.json"


def _kline_server_cache_path(symbol: str, kline: BacktestKline | str, periods: int, days: int) -> str:
    name = _kline_server_cache_filename(symbol, str(kline), periods, days)
    return os.path.normpath(os.path.join(KLINE_SERVER_CACHE_DIR, name))


def _bars_to_cache_items(bars: list[Bar]) -> list[dict[str, Any]]:
    return [
        {
            "date": b.date.isoformat() if hasattr(b.date, "isoformat") else str(b.date)[:10],
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume),
        }
        for b in bars
    ]


def _items_to_bars_for_cache(items: list[dict[str, Any]]) -> list[Bar]:
    out: list[Bar] = []
    for x in items:
        if not isinstance(x, dict):
            continue
        try:
            out.append(
                Bar(
                    date=coerce_bar_datetime(x.get("date", "")),
                    open=float(x.get("open", 0.0) or 0.0),
                    high=float(x.get("high", 0.0) or 0.0),
                    low=float(x.get("low", 0.0) or 0.0),
                    close=float(x.get("close", 0.0) or 0.0),
                    volume=float(x.get("volume", 0.0) or 0.0),
                )
            )
        except Exception:
            continue
    out.sort(key=lambda b: b.date)
    return out


def _read_server_kline_cache_file(path: str) -> tuple[list[Bar] | None, dict[str, Any]]:
    try:
        if not os.path.isfile(path):
            return None, {}
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return None, {}
    if not isinstance(raw, dict):
        return None, {}
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        return None, meta
    bars = _items_to_bars_for_cache(items)
    return (bars if bars else None), meta


def _bar_calendar_date(b: Bar) -> date:
    """Bar 时间戳对应的日历日（用于按自然日窗口过滤）。"""
    dt = b.date
    if isinstance(dt, datetime):
        return dt.date()
    if isinstance(dt, date):
        return dt
    try:
        coerced = coerce_bar_datetime(dt)
        if isinstance(coerced, datetime):
            return coerced.date()
    except Exception:
        pass
    return date.today()


def _estimate_bars_upper_bound_calendar(days: int, kline: str) -> int:
    """估算「日历窗口」内 K 线根数上界，供分页拉取。"""
    d = max(1, min(_MAX_CALENDAR_DAYS_SINGLE_FETCH, int(days)))
    kl = str(kline or "1d").strip().lower()
    mult = {
        "1m": 420,
        "5m": 90,
        "10m": 45,
        "30m": 22,
        "1h": 14,
        "2h": 9,
        "4h": 8,
        "1d": 2,
    }.get(kl, 2)
    est = d * mult + 120
    return max(250, est)


def _filter_bars_calendar_window(bars: list[Bar], start: date) -> list[Bar]:
    out = [b for b in bars if _bar_calendar_date(b) >= start]
    out.sort(key=lambda x: x.date)
    return out


def _bars_cover_calendar_start(bars: list[Bar], start: date, slack_days: int = 5) -> bool:
    """最早一根是否落在窗口起点附近（允许长假/周末导致首根略晚几天）。"""
    if not bars:
        return False
    m = min(_bar_calendar_date(b) for b in bars)
    return m <= start + timedelta(days=slack_days)


_USE_SERVER_CACHE_FOR_CALENDAR_FETCH = str(os.getenv("LONGPORT_USE_SERVER_KLINE_CACHE", "1")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _fetch_bars_calendar_days(
    symbol: str,
    days: int,
    kline: BacktestKline = "1d",
    *,
    _skip_gateway: bool = False,
) -> list[Bar]:
    """按日历天数拉取 K 线：可选先走 LONGPORT 网关（与单次 _fetch_bars 一致），再读服务器缓存，否则分页 SDK 拉取。

    解决：仅分页 SDK 拉取时，API 进程无行情上下文但 Worker 网关有数据 → ML 矩阵 / 研究拉 K 线为 empty 的问题。
    internal_longport_history_bars 在已尝试过网关后应传入 _skip_gateway=True，避免重复请求。
    """
    import logging

    logger = logging.getLogger(__name__)
    sym = str(symbol or "").strip().upper()
    if not sym:
        return []
    if _is_symbol_marked_invalid(sym):
        return []
    ds = max(1, min(_MAX_CALENDAR_DAYS_SINGLE_FETCH, int(days)))
    cache_key = _longport_bars_mem_cache_key("calendar", sym, ds, str(kline))
    cached = _longport_bars_mem_cache_get(cache_key)
    if cached is not None:
        return cached
    inflight_ev, inflight_owner = _longport_bars_inflight_enter(cache_key)
    if not inflight_owner:
        bars, err = _longport_bars_inflight_await(cache_key, inflight_ev, timeout_seconds=25.0)
        if bars is not None:
            if bars:
                _longport_bars_mem_cache_put(cache_key, bars)
            if err:
                raise RuntimeError(err)
            return bars
    start = date.today() - timedelta(days=ds)

    try:
        if not _skip_gateway:
            need_est = _estimate_bars_upper_bound_calendar(ds, str(kline))
            gw = _gateway_get_json(
                "/internal/longport/history-bars",
                {"symbol": sym, "days": int(ds), "kline": str(kline)},
                timeout=max(LONGPORT_GATEWAY_TIMEOUT_SECONDS, 20.0),
            )
            if isinstance(gw, dict) and isinstance(gw.get("items"), list):
                items = gw.get("items") or []
                suspicious_truncation = 999 <= len(items) <= 1010 and need_est > 1100
                if items and not suspicious_truncation:
                    gw_bars = _history_json_items_to_bars(items)
                    if gw_bars:
                        filtered_gw = _filter_bars_calendar_window(gw_bars, start)
                        use_gw = filtered_gw if filtered_gw else gw_bars
                        if use_gw:
                            _longport_bars_mem_cache_put(cache_key, use_gw)
                            if inflight_owner:
                                _longport_bars_inflight_resolve(cache_key, bars=use_gw)
                            return use_gw

        if _USE_SERVER_CACHE_FOR_CALENDAR_FETCH:
            try:
                path = _kline_server_cache_path(sym, kline, 0, ds)
                cached, _meta = _read_server_kline_cache_file(path)
                if cached:
                    filtered = _filter_bars_calendar_window(cached, start)
                    if filtered and _bars_cover_calendar_start(filtered, start):
                        logger.info(
                            "kline calendar: server cache hit %s bars=%s window>=%s",
                            os.path.basename(path),
                            len(filtered),
                            start,
                        )
                        _longport_bars_mem_cache_put(cache_key, filtered)
                        if inflight_owner:
                            _longport_bars_inflight_resolve(cache_key, bars=filtered)
                        return filtered
            except Exception as e:
                logger.debug("server kline cache read skipped: %s", e)

        target = _estimate_bars_upper_bound_calendar(ds, str(kline))
        try:
            raw = _fetch_bars_paginated(sym, target, kline)
        except Exception as e:
            logger.warning("kline calendar: paginated fetch failed %s, fallback _fetch_bars: %s", sym, e)
            try:
                fallback = _filter_bars_calendar_window(_fetch_bars(sym, ds, kline), start)
                if fallback:
                    _longport_bars_mem_cache_put(cache_key, fallback)
                if inflight_owner:
                    _longport_bars_inflight_resolve(cache_key, bars=fallback)
                return fallback
            except Exception as fallback_err:
                if _is_longport_connect_error(fallback_err):
                    logger.warning("kline calendar: fallback fetch failed by connect error %s: %s", sym, fallback_err)
                    throttled_reset_contexts(reset_contexts, _RUNTIME_STATE)
                    if inflight_owner:
                        _longport_bars_inflight_resolve(cache_key, bars=[])
                    return []
                raise

        filtered = _filter_bars_calendar_window(raw, start)
        if not filtered:
            if raw:
                _longport_bars_mem_cache_put(cache_key, raw)
            if inflight_owner:
                _longport_bars_inflight_resolve(cache_key, bars=raw)
            return raw
        if not _bars_cover_calendar_start(filtered, start) and len(raw) >= 4990:
            logger.warning(
                "kline calendar: possible truncation sym=%s kline=%s days=%s bars=%s first_date=%s need_start=%s",
                sym,
                kline,
                ds,
                len(filtered),
                min(_bar_calendar_date(b) for b in filtered),
                start,
            )
        _longport_bars_mem_cache_put(cache_key, filtered)
        if inflight_owner:
            _longport_bars_inflight_resolve(cache_key, bars=filtered)
        return filtered
    except Exception as e:
        if inflight_owner:
            _longport_bars_inflight_resolve(cache_key, error=str(e))
        raise


def _write_server_kline_cache_file(
    path: str,
    *,
    symbol: str,
    kline: str,
    periods: int,
    days: int,
    bars: list[Bar],
) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "meta": {
            "symbol": str(symbol).strip().upper(),
            "kline": str(kline),
            "periods": int(periods),
            "days": int(days),
            "bar_count": len(bars),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
        "items": _bars_to_cache_items(bars),
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=0)
        f.write("\n")
    os.replace(tmp, path)


def _load_bars_from_server_kline_cache(
    symbol: str,
    kline: BacktestKline,
    periods: int,
    days: int,
) -> list[Bar]:
    path = _kline_server_cache_path(symbol, kline, periods, days)
    bars, _meta = _read_server_kline_cache_file(path)
    if not bars:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "kline_server_cache_miss",
                "message": "服务器上暂无该组合的 K 线缓存，请先调用 POST /backtest/kline-cache/fetch 或在回测页点击「下载K线到服务器」。",
                "cache_path": path,
            },
        )
    if periods > 0:
        if len(bars) < periods:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "kline_server_cache_incomplete",
                    "message": f"缓存仅 {len(bars)} 根，少于请求的 periods={periods}，请 force_refresh 重新下载。",
                    "bar_count": len(bars),
                    "periods": periods,
                },
            )
        return bars[-periods:] if len(bars) > periods else bars
    return bars


def _resolve_bars_for_backtest_compare(
    symbol: str,
    periods: int,
    days: int,
    kline: BacktestKline,
    client_bars: list[Bar] | None,
    *,
    use_server_kline_cache: bool = False,
) -> list[Bar]:
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol_required")
    if client_bars is not None and len(client_bars) > 0:
        bars = client_bars
        if periods > 0 and len(bars) > periods:
            bars = bars[-periods:]
    elif use_server_kline_cache:
        bars = _load_bars_from_server_kline_cache(sym, kline, periods, days)
    elif periods > 0:
        bars = _fetch_bars_by_periods(sym, periods, kline)
        bars = bars[-periods:] if len(bars) > periods else bars
    else:
        bars = _fetch_bars_calendar_days(sym, days, kline)
    if not bars:
        raise HTTPException(status_code=400, detail="无法获取历史数据")
    return bars


def _backtest_compare_core(
    symbol: str,
    bars: list[Bar],
    *,
    periods: int,
    days: int,
    kline: BacktestKline,
    initial_capital: float,
    execution_mode: Literal["next_open", "bar_close"],
    slippage_bps: float,
    commission_bps: float | None,
    stamp_duty_bps: float | None,
    walk_forward_windows: int,
    ml_filter_enabled: bool,
    ml_model_type: Literal["logreg", "random_forest", "gbdt"],
    ml_threshold: float,
    ml_horizon_days: int,
    ml_train_ratio: float,
    include_trades: bool,
    trade_limit: int,
    trade_offset: int,
    strategy_key: str | None,
    include_best_kline: bool,
    strategy_params_map: dict[str, dict[str, Any]] | None,
    include_bars_in_response: bool,
) -> dict[str, Any]:
    if execution_mode not in {"next_open", "bar_close"}:
        raise HTTPException(status_code=400, detail="execution_mode 仅支持 next_open / bar_close")
    slippage_bps = max(0.0, min(float(slippage_bps), 200.0))
    if commission_bps is not None:
        commission_bps = max(0.0, min(float(commission_bps), 500.0))
    if stamp_duty_bps is not None:
        stamp_duty_bps = max(0.0, min(float(stamp_duty_bps), 500.0))
    walk_forward_windows = max(1, min(int(walk_forward_windows), 12))
    ml_threshold = max(0.5, min(float(ml_threshold), 0.95))
    ml_horizon_days = max(1, min(int(ml_horizon_days), 30))
    ml_train_ratio = max(0.5, min(float(ml_train_ratio), 0.9))
    trade_limit = max(1, min(int(trade_limit), 500))
    trade_offset = max(0, int(trade_offset))

    ml_prob_map: dict[str, float] = {}
    ml_walk_forward: dict[str, Any] = {}
    if ml_filter_enabled:
        try:
            effective_commission_bps = float(commission_bps) if commission_bps is not None else 3.0
            effective_stamp_duty_bps = float(stamp_duty_bps) if stamp_duty_bps is not None else 0.0
            estimated_round_trip_cost_bps = max(
                0.0,
                (float(slippage_bps) + effective_commission_bps) * 2.0 + effective_stamp_duty_bps,
            )
            ml_prob_map, ml_walk_forward = _build_ml_probability_map(
                bars=bars,
                model_type=ml_model_type,
                horizon_days=ml_horizon_days,
                transaction_cost_bps=estimated_round_trip_cost_bps,
                train_ratio=ml_train_ratio,
                walk_forward_windows=walk_forward_windows,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"ML过滤器初始化失败: {e}")

    all_strategies = list_strategy_names()
    if strategy_key:
        s = str(strategy_key).strip().lower()
        if s not in all_strategies:
            raise HTTPException(status_code=400, detail=f"未知策略: {strategy_key}")
        strategies = [s]
    else:
        strategies = all_strategies
    results: list[dict[str, Any]] = []
    curves: dict[str, dict[str, Any]] = {}
    trades_by_strategy: dict[str, list[Any]] = {}

    def _params_for(name: str) -> dict[str, Any] | None:
        if not strategy_params_map:
            return None
        raw = strategy_params_map.get(name)
        return raw if isinstance(raw, dict) else None

    def _run_engine_for(strategy_name: str, bars_slice):
        sp = _params_for(strategy_name)
        sfn = get_strategy(strategy_name, sp)

        def _signal_filter(action: str, bars_so_far: list[Bar], position: int) -> bool:
            if not ml_filter_enabled:
                return True
            if action != "buy":
                return True
            d = str(bars_so_far[-1].date)
            p = ml_prob_map.get(d)
            if p is None:
                return False
            return p >= ml_threshold

        engine = BacktestEngine(
            bars=bars_slice,
            symbol=symbol,
            strategy_name=sfn.__name__,
            strategy_fn=sfn,
            initial_capital=initial_capital,
            execution_mode=execution_mode,
            slippage_bps=slippage_bps,
            commission_bps=commission_bps,
            stamp_duty_bps=stamp_duty_bps,
            signal_filter=_signal_filter if ml_filter_enabled else None,
        )
        return engine.run()

    for sname in strategies:
        try:
            r = _run_engine_for(sname, bars)
            peak = 0.0
            points: list[dict[str, Any]] = []
            for pt in r.equity_curve:
                eq = float(pt["equity"])
                peak = max(peak, eq)
                dd = ((peak - eq) / peak * 100) if peak > 0 else 0.0
                points.append(
                    {
                        "date": pt["date"],
                        "equity": round(eq, 2),
                        "drawdown_pct": round(dd, 2),
                    }
                )
            curves[r.strategy_name] = {"strategy": r.strategy_name, "points": points}
            trades_by_strategy[r.strategy_name] = list(r.trades or [])

            row = {
                "strategy_key": sname,
                "strategy": r.strategy_name,
                "total_return_pct": round(r.total_return_pct, 2),
                "annual_return_pct": round(r.annual_return_pct, 2),
                "max_drawdown_pct": round(r.max_drawdown_pct, 2),
                "sharpe_ratio": round(r.sharpe_ratio, 2),
                "win_rate_pct": round(r.win_rate_pct, 2),
                "profit_factor": round(r.profit_factor, 2),
                "total_trades": r.total_trades,
                "total_commission": round(r.total_commission, 2),
                "total_stamp_duty": round(r.total_stamp_duty, 2),
                "total_cost_pct_initial": round(r.total_cost_pct_initial, 4),
                "fee_breakdown": {k: round(float(v), 2) for k, v in (r.fee_breakdown or {}).items()},
            }
            if include_trades:
                trades_all = [
                    {
                        "symbol": t.symbol,
                        "entry_date": str(t.entry_date),
                        "exit_date": str(t.exit_date),
                        "entry_price": round(float(t.entry_price), 4),
                        "exit_price": round(float(t.exit_price), 4),
                        "quantity": int(t.quantity),
                        "direction": t.direction,
                        "pnl": round(float(t.pnl), 2),
                        "pnl_pct": round(float(t.pnl_pct), 2),
                        "hold_days": int(t.hold_days),
                    }
                    for t in r.trades
                ]
                row["trades"] = trades_all[trade_offset: trade_offset + trade_limit]
                row["trades_pagination"] = {
                    "offset": trade_offset,
                    "limit": trade_limit,
                    "total": len(trades_all),
                    "has_more": (trade_offset + trade_limit) < len(trades_all),
                }

            if walk_forward_windows > 1 and len(bars) >= walk_forward_windows * 40:
                win_size = len(bars) // walk_forward_windows
                wf_returns: list[float] = []
                wf_sharpes: list[float] = []
                wf_drawdowns: list[float] = []
                for i in range(walk_forward_windows):
                    start = i * win_size
                    end = (i + 1) * win_size if i < walk_forward_windows - 1 else len(bars)
                    seg = bars[start:end]
                    if len(seg) < 30:
                        continue
                    wr = _run_engine_for(sname, seg)
                    wf_returns.append(float(wr.total_return_pct))
                    wf_sharpes.append(float(wr.sharpe_ratio))
                    wf_drawdowns.append(float(wr.max_drawdown_pct))
                if wf_returns:
                    row["wf_avg_return_pct"] = round(sum(wf_returns) / len(wf_returns), 2)
                    row["wf_avg_sharpe"] = round(sum(wf_sharpes) / len(wf_sharpes), 2)
                    row["wf_avg_max_drawdown_pct"] = round(sum(wf_drawdowns) / len(wf_drawdowns), 2)
                    row["wf_positive_windows"] = sum(1 for x in wf_returns if x > 0)
                    row["wf_windows"] = len(wf_returns)

            results.append(row)
        except Exception as e:
            results.append({"strategy": sname, "error": str(e)})
    results.sort(
        key=lambda x: (
            x.get("wf_avg_return_pct", -9999),
            x.get("total_return_pct", -9999),
        ),
        reverse=True,
    )
    best_curve = None
    for row in results:
        if "error" in row:
            continue
        best_curve = curves.get(row["strategy"])
        if best_curve:
            break
    best_kline = None
    if include_best_kline and best_curve:
        best_strategy_name = str(best_curve.get("strategy", ""))
        best_trades = trades_by_strategy.get(best_strategy_name, [])
        buy_marks: list[dict[str, Any]] = []
        sell_marks: list[dict[str, Any]] = []
        for t in best_trades:
            buy_marks.append(
                {
                    "date": str(t.entry_date),
                    "price": round(float(t.entry_price), 4),
                    "quantity": int(t.quantity),
                }
            )
            sell_marks.append(
                {
                    "date": str(t.exit_date),
                    "price": round(float(t.exit_price), 4),
                    "quantity": int(t.quantity),
                    "pnl": round(float(t.pnl), 2),
                    "pnl_pct": round(float(t.pnl_pct), 2),
                }
            )
        best_kline = {
            "strategy": best_strategy_name,
            "dates": [str(b.date) for b in bars],
            "ohlc": [
                [
                    round(float(b.open), 4),
                    round(float(b.close), 4),
                    round(float(b.low), 4),
                    round(float(b.high), 4),
                ]
                for b in bars
            ],
            "buy_marks": buy_marks,
            "sell_marks": sell_marks,
        }
    benchmark_curve = None
    if bars:
        base_close = float(bars[0].close) if float(bars[0].close) > 0 else 0.0
        if base_close > 0:
            benchmark_points: list[dict[str, Any]] = []
            for b in bars:
                bench_equity = float(initial_capital) * (float(b.close) / base_close)
                benchmark_points.append(
                    {
                        "date": str(b.date),
                        "equity": round(bench_equity, 2),
                    }
                )
            benchmark_curve = {
                "strategy": "buy_and_hold",
                "points": benchmark_points,
            }
    out: dict[str, Any] = {
        "symbol": symbol,
        "days": days,
        "periods": periods if periods > 0 else len(bars),
        "kline": kline,
        "initial_capital": initial_capital,
        "execution": {
            "mode": execution_mode,
            "slippage_bps": slippage_bps,
            "commission_bps_override": commission_bps,
            "stamp_duty_bps_override": stamp_duty_bps,
        },
        "ml_filter": {
            "enabled": ml_filter_enabled,
            "model_type": ml_model_type,
            "threshold": ml_threshold,
            "horizon_days": ml_horizon_days,
            "train_ratio": ml_train_ratio,
            "probability_coverage": len(ml_prob_map),
            "label_transaction_cost_bps": round(
                max(
                    0.0,
                    (float(slippage_bps) + (float(commission_bps) if commission_bps is not None else 3.0)) * 2.0
                    + (float(stamp_duty_bps) if stamp_duty_bps is not None else 0.0),
                ),
                4,
            ),
            "walk_forward": ml_walk_forward,
        },
        "walk_forward_windows": walk_forward_windows,
        "results": results,
        "best_curve": best_curve,
        "benchmark_curve": benchmark_curve,
        "best_kline": best_kline,
        "strategy_params_applied": strategy_params_map or {},
    }
    if include_bars_in_response:
        out["bars_snapshot"] = [
            {
                "date": b.date.isoformat() if hasattr(b.date, "isoformat") else str(b.date),
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
            }
            for b in bars
        ]
    return out


def auto_trader_strong_stocks(
    market: Literal["us", "hk", "cn"] = "us",
    limit: int = 8,
    kline: BacktestKline = "1d",
) -> dict[str, Any]:
    started = time.perf_counter()
    market_norm = str(market or "us").lower()
    cache_key = _strong_stocks_cache_key(str(market_norm), max(1, int(limit)), str(kline))
    runtime = _auto_trader_runtime_status()
    summary = runtime.get("last_scan_summary") if isinstance(runtime, dict) else None
    worker_last_scan_summary_at = summary.get("scan_time") if isinstance(summary, dict) else None
    cached_fresh = _strong_stocks_cache_get(cache_key, allow_stale=False)
    if cached_fresh:
        diagnostics = cached_fresh.get("diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
            cached_fresh["diagnostics"] = diagnostics
        diagnostics["cache_hit"] = True
        diagnostics["cache_stale"] = False
        diagnostics["rate_limited"] = False
        emit_metric(
            event="api.auto_trader.strong_stocks",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market), "kline": str(kline), "source": "cache_fresh"},
            extra={"count": int(cached_fresh.get("count", 0))},
        )
        return _strong_stocks_response_attach_worker_scan_time(cached_fresh, worker_last_scan_summary_at)
    rows = summary.get("strong_stocks", []) if isinstance(summary, dict) else []
    if not isinstance(rows, list):
        rows = []
    if market_norm == "us":
        rows = [x for x in rows if str(x.get("symbol", "")).upper().endswith(".US")]
    elif market_norm == "hk":
        rows = [x for x in rows if str(x.get("symbol", "")).upper().endswith(".HK")]
    elif market_norm == "cn":
        rows = [
            x
            for x in rows
            if str(x.get("symbol", "")).upper().endswith(".SH") or str(x.get("symbol", "")).upper().endswith(".SZ")
        ]
    skipped = summary.get("skipped", {}) if isinstance(summary, dict) else {}
    if not isinstance(skipped, dict):
        skipped = {}
    decision_log = summary.get("decision_log", []) if isinstance(summary, dict) else []
    if not isinstance(decision_log, list):
        decision_log = []
    score_error_examples: list[dict[str, Any]] = []
    for row in decision_log:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason", ""))
        if "score_error" not in reason:
            continue
        score_error_examples.append(
            {
                "symbol": str(row.get("symbol", "")),
                "side": str(row.get("side", "")),
                "reason": reason,
            }
        )
        if len(score_error_examples) >= 5:
            break
    worker_runtime = runtime.get("worker", {}) if isinstance(runtime, dict) else {}
    if not isinstance(worker_runtime, dict):
        worker_runtime = {}
    source = "worker_last_scan_summary"
    if rows:
        out = {
            "market": market,
            "kline": kline,
            "count": min(len(rows), max(1, int(limit))),
            "items": rows[: max(1, int(limit))],
            "source": source,
            "scan_time": summary.get("scan_time") if isinstance(summary, dict) else None,
            "worker_running": bool(runtime.get("worker_running")) if isinstance(runtime, dict) else False,
            "diagnostics": {
                "strong_count": summary.get("strong_count", 0) if isinstance(summary, dict) else 0,
                "score_error": int(skipped.get("score_error", 0) or 0),
                "no_signal": int(skipped.get("no_signal", 0) or 0),
                "ml_filter": int(skipped.get("ml_filter", 0) or 0),
                "duplicate_guard": int(skipped.get("duplicate_guard", 0) or 0),
                "last_manual_scan_error": worker_runtime.get("last_manual_scan_error"),
                "worker_updated_at": worker_runtime.get("updated_at"),
                "score_error_examples": score_error_examples,
                "invalid_symbol_errors": (summary.get("invalid_symbol_errors", []) if isinstance(summary, dict) else [])[:5],
                "fallback_used": False,
                "cache_hit": False,
                "cache_stale": False,
                "rate_limited": False,
                "refresh_scheduled": False,
            },
        }
        _strong_stocks_cache_put(cache_key, out)
        emit_metric(
            event="api.auto_trader.strong_stocks",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market), "kline": str(kline), "source": source},
            extra={"count": int(out.get("count", 0))},
        )
        return _strong_stocks_response_attach_worker_scan_time(out, worker_last_scan_summary_at)

    refresh_scheduled = _schedule_strong_stocks_refresh(cache_key, market_norm, max(1, int(limit)), str(kline))
    cached_stale = _strong_stocks_cache_get(cache_key, allow_stale=True)
    if cached_stale:
        diagnostics = cached_stale.get("diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
            cached_stale["diagnostics"] = diagnostics
        diagnostics["cache_hit"] = True
        diagnostics["cache_stale"] = True
        diagnostics["fallback_used"] = True
        diagnostics["refresh_scheduled"] = refresh_scheduled
        emit_metric(
            event="api.auto_trader.strong_stocks",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market), "kline": str(kline), "source": "cache_stale_async_refresh"},
            extra={"count": int(cached_stale.get("count", 0)), "refresh_scheduled": bool(refresh_scheduled)},
        )
        return _strong_stocks_response_attach_worker_scan_time(cached_stale, worker_last_scan_summary_at)

    out = {
        "market": market,
        "kline": kline,
        "count": 0,
        "items": [],
        "source": "no_data_async_refresh",
        "scan_time": summary.get("scan_time") if isinstance(summary, dict) else None,
        "worker_running": bool(runtime.get("worker_running")) if isinstance(runtime, dict) else False,
        "diagnostics": {
            "strong_count": summary.get("strong_count", 0) if isinstance(summary, dict) else 0,
            "score_error": int(skipped.get("score_error", 0) or 0),
            "no_signal": int(skipped.get("no_signal", 0) or 0),
            "ml_filter": int(skipped.get("ml_filter", 0) or 0),
            "duplicate_guard": int(skipped.get("duplicate_guard", 0) or 0),
            "last_manual_scan_error": worker_runtime.get("last_manual_scan_error"),
            "worker_updated_at": worker_runtime.get("updated_at"),
            "score_error_examples": score_error_examples,
            "invalid_symbol_errors": (summary.get("invalid_symbol_errors", []) if isinstance(summary, dict) else [])[:5],
            "fallback_used": True,
            "cache_hit": False,
            "cache_stale": False,
            "rate_limited": False,
            "refresh_scheduled": refresh_scheduled,
        },
    }
    emit_metric(
        event="api.auto_trader.strong_stocks",
        ok=True,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        tags={"market": str(market), "kline": str(kline), "source": "no_data_async_refresh"},
        extra={"count": 0, "refresh_scheduled": bool(refresh_scheduled)},
    )
    return _strong_stocks_response_attach_worker_scan_time(out, worker_last_scan_summary_at)


def auto_trader_strategy_score(
    symbol: str,
    days: int = 120,
    kline: BacktestKline = "1d",
) -> dict[str, Any]:
    raise HTTPException(
        status_code=409,
        detail={
            "error": "worker_mode_only",
            "message": "策略打分已迁移到独立进程，API 主进程不再直接调用自动交易引擎。",
            "suggestion": "请通过 worker 运行周期扫描，不在 API 进程做即时评分。",
        },
    )


def auto_trader_strategies() -> dict[str, Any]:
    items = list_strategy_metadata()
    return {"count": len(items), "items": items}


def auto_trader_pair_backtest(
    market: Literal["us", "hk", "cn"] = "us",
    days: int = 180,
    kline: BacktestKline = "1d",
    initial_capital: float = 100000.0,
) -> dict[str, Any]:
    raise HTTPException(
        status_code=409,
        detail={
            "error": "worker_mode_only",
            "message": "组合回测已从 API 主进程下线，避免触发重型计算导致服务抖动。",
        },
    )


def auto_trader_scan_run() -> dict[str, Any]:
    started = time.perf_counter()
    if not _is_auto_trader_supervisor_running():
        emit_metric(
            event="api.auto_trader.scan_run",
            ok=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            extra={"error": "worker_not_running"},
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "worker_not_running",
                "message": "自动交易进程未运行，请先在 Setup 启动自动交易。",
            },
        )
    try:
        with open(AUTO_TRADER_WORKER_TRIGGER_SCAN_FILE, "w", encoding="utf-8") as f:
            f.write(datetime.now().isoformat())
    except Exception as e:
        emit_metric(
            event="api.auto_trader.scan_run",
            ok=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            extra={"error": str(e)},
        )
        raise HTTPException(status_code=500, detail=f"manual_scan_trigger_failed: {e}") from e
    out = {"ok": True, "accepted": True, "mode": "worker_triggered"}
    emit_metric(
        event="api.auto_trader.scan_run",
        ok=True,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        extra={"accepted": True},
    )
    return out


def auto_trader_signals(status: str = "all") -> dict[str, Any]:
    mem_items = auto_trader.list_signals(status=status)
    disk_items = load_persisted_signals(status=status)

    merged: dict[str, dict[str, Any]] = {}
    for s in disk_items:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("signal_id") or "")
        if not sid:
            continue
        merged[sid] = s
    for s in mem_items:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("signal_id") or "")
        if not sid:
            continue
        merged.setdefault(sid, s)

    def _sort_key(x: dict[str, Any]) -> str:
        return str(x.get("created_at") or x.get("updated_at") or "")

    items = sorted(merged.values(), key=_sort_key, reverse=True)
    return {"status": status, "items": items}


def _enqueue_worker_confirm(signal_id: str) -> None:
    """
    将用户确认请求投递给 Worker（文件队列）。
    Worker 消费该队列并调用 AutoTraderService.confirm_and_execute。
    """
    try:
        signal_id = str(signal_id or "").strip()
        if not signal_id:
            return
        with _AUTO_TRADER_CONFIRM_QUEUE_LOCK:
            ids: list[str] = []
            if os.path.exists(AUTO_TRADER_WORKER_CONFIRM_SIGNALS_FILE):
                try:
                    raw = open(AUTO_TRADER_WORKER_CONFIRM_SIGNALS_FILE, "r", encoding="utf-8").read()
                    if raw.strip():
                        data = json.loads(raw)
                        if isinstance(data, dict):
                            sigs = data.get("signal_ids") or data.get("signals") or []
                        else:
                            sigs = data
                        if isinstance(sigs, list):
                            ids = [str(x) for x in sigs if str(x).strip()]
                except Exception:
                    ids = []
            if signal_id not in ids:
                ids.append(signal_id)
            # 小生产：直接覆盖写入，文件队列只期望少量并发
            payload = {"updated_at": datetime.now().isoformat(), "signal_ids": ids[:200]}
            tmp = AUTO_TRADER_WORKER_CONFIRM_SIGNALS_FILE + f".tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, AUTO_TRADER_WORKER_CONFIRM_SIGNALS_FILE)
    except Exception:
        pass


def auto_trader_confirm(signal_id: str, body: AutoTraderConfirmBody) -> dict[str, Any]:
    runtime = _auto_trader_runtime_status()
    if not runtime.get("worker_running"):
        raise HTTPException(
            status_code=409,
            detail={"error": "worker_not_running", "message": "自动交易进程未运行，请先在 Setup 启动自动交易。"},
        )

    _enqueue_worker_confirm(signal_id)

    # 等待 Worker 落盘更新（避免前端立刻刷新仍看不到已确认状态）
    deadline = time.time() + 15.0
    while time.time() < deadline:
        try:
            rows = load_persisted_signals(status="all")
        except Exception:
            rows = []
        for s in rows:
            if str(s.get("signal_id") or "") != str(signal_id or ""):
                continue
            st = s.get("status")
            if st and st != "pending":
                return {"ok": True, "signal": s}
        time.sleep(0.5)

    return {"ok": True, "queued": True, "signal_id": signal_id}


def _put_research_task(task_id: str, row: dict[str, Any]) -> None:
    with _RESEARCH_TASKS_LOCK:
        _RESEARCH_TASKS[task_id] = row
        if len(_RESEARCH_TASKS) > _RESEARCH_TASK_MAX_KEEP:
            keys = sorted(_RESEARCH_TASKS.keys(), key=lambda x: str(_RESEARCH_TASKS[x].get("created_at", "")))
            for k in keys[: max(0, len(_RESEARCH_TASKS) - _RESEARCH_TASK_MAX_KEEP)]:
                _RESEARCH_TASKS.pop(k, None)


def _clamp_progress_pct(v: Any) -> int:
    try:
        n = int(float(v))
    except Exception:
        n = 0
    return max(0, min(100, n))


def _set_task_progress(
    row: dict[str, Any],
    *,
    pct: int,
    stage: str,
    text: str,
) -> None:
    row["progress_pct"] = _clamp_progress_pct(pct)
    row["progress_stage"] = str(stage or "running")
    row["progress_text"] = str(text or "")
    row["progress_updated_at"] = datetime.now().isoformat()


def _update_task_progress_by_elapsed(
    row: dict[str, Any],
    *,
    started_perf: float,
    expected_seconds: float,
    base_pct: int,
    cap_pct: int,
    stage: str,
    text_prefix: str,
) -> None:
    exp = max(1.0, float(expected_seconds))
    ratio = min(1.0, max(0.0, (time.perf_counter() - started_perf) / exp))
    pct = int(round(base_pct + (cap_pct - base_pct) * ratio))
    _set_task_progress(
        row,
        pct=pct,
        stage=stage,
        text=f"{text_prefix}（{pct}%）",
    )


def _get_research_task(task_id: str) -> Optional[dict[str, Any]]:
    with _RESEARCH_TASKS_LOCK:
        row = _RESEARCH_TASKS.get(task_id)
        return dict(row) if isinstance(row, dict) else None


def _list_research_tasks() -> list[dict[str, Any]]:
    with _RESEARCH_TASKS_LOCK:
        out: list[dict[str, Any]] = []
        for row in _RESEARCH_TASKS.values():
            if isinstance(row, dict):
                out.append(dict(row))
        return out


def _queued_task_positions(rows: list[dict[str, Any]]) -> dict[str, int]:
    queued_rows = [
        r for r in rows if str(r.get("status", "")).lower() == "queued" and str(r.get("task_id") or "").strip()
    ]
    queued_rows.sort(key=lambda r: str(r.get("created_at") or ""))
    return {str(r.get("task_id")): i + 1 for i, r in enumerate(queued_rows)}


def _count_active_research_tasks() -> int:
    rows = _list_research_tasks()
    return sum(1 for r in rows if str(r.get("status", "")).lower() in {"queued", "running"})


def _find_duplicate_async_task(task_type: str, params_subset: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = _list_research_tasks()
    for row in rows:
        if str(row.get("task_type", "")).lower() != str(task_type).lower():
            continue
        status = str(row.get("status", "")).lower()
        if status not in {"queued", "running"}:
            continue
        params = row.get("params")
        if not isinstance(params, dict):
            continue
        same = True
        for k, v in params_subset.items():
            if str(params.get(k)) != str(v):
                same = False
                break
        if same:
            return row
    return None


def _strategy_matrix_task_params(
    cfg: Any,
    *,
    market: str,
    top_n: int,
    max_strategies: int,
    max_drawdown_limit_pct: float,
    min_symbols_used: int,
    strategy_pool_mode: str,
    matrix_overrides: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """异步策略矩阵任务的去重键与 params 存储：必须包含会影响 run_strategy_param_matrix 结果的配置，否则会误判 duplicate 并长期复用旧结果文件。"""
    c = cfg if isinstance(cfg, dict) else {}
    kl = str(c.get("kline") or "1d")
    try:
        bd = int(c.get("backtest_days", 180) or 180)
    except (TypeError, ValueError):
        bd = 180
    mo = matrix_overrides if isinstance(matrix_overrides, dict) else {}
    mo_sig = json.dumps(mo, sort_keys=True, ensure_ascii=False, default=str) if mo else ""
    return {
        "market": str(market),
        "top_n": max(8, min(30, int(top_n))),
        "strategy_pool_mode": str(strategy_pool_mode),
        "max_strategies": max(6, min(20, int(max_strategies))),
        "max_drawdown_limit_pct": max(1.0, min(80.0, float(max_drawdown_limit_pct))),
        "min_symbols_used": max(3, min(30, int(min_symbols_used))),
        "kline": kl,
        "backtest_days": bd,
        "matrix_overrides_sig": mo_sig,
    }


def _mark_backend_busy(reason: str) -> None:
    payload = {
        "reason": str(reason or "research"),
        "updated_at": datetime.now().isoformat(),
        "pid": os.getpid(),
    }
    try:
        with open(WATCHDOG_BUSY_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except Exception:
        pass


def _clear_backend_busy() -> None:
    try:
        if os.path.exists(WATCHDOG_BUSY_FILE):
            os.remove(WATCHDOG_BUSY_FILE)
    except Exception:
        pass


def _research_busy_enter(reason: str) -> None:
    global _RESEARCH_BUSY_ACTIVE
    global _RESEARCH_BUSY_HEARTBEAT_THREAD
    with _RESEARCH_BUSY_LOCK:
        _RESEARCH_BUSY_ACTIVE += 1
        _RUNTIME_STATE.research_busy_active = int(_RESEARCH_BUSY_ACTIVE)
        _mark_backend_busy(reason)
        if _RESEARCH_BUSY_ACTIVE == 1:
            _RESEARCH_BUSY_HEARTBEAT_STOP.clear()
            if _RESEARCH_BUSY_HEARTBEAT_THREAD is None or not _RESEARCH_BUSY_HEARTBEAT_THREAD.is_alive():
                def _busy_heartbeat_loop() -> None:
                    while not _RESEARCH_BUSY_HEARTBEAT_STOP.wait(_RESEARCH_BUSY_HEARTBEAT_INTERVAL_SECONDS):
                        with _RESEARCH_BUSY_LOCK:
                            if _RESEARCH_BUSY_ACTIVE <= 0:
                                break
                        _mark_backend_busy("research_busy_heartbeat")

                _RESEARCH_BUSY_HEARTBEAT_THREAD = threading.Thread(
                    target=_busy_heartbeat_loop,
                    name="research_busy_heartbeat",
                    daemon=True,
                )
                _RESEARCH_BUSY_HEARTBEAT_THREAD.start()


def _research_busy_leave() -> None:
    global _RESEARCH_BUSY_ACTIVE
    global _RESEARCH_BUSY_HEARTBEAT_THREAD
    with _RESEARCH_BUSY_LOCK:
        _RESEARCH_BUSY_ACTIVE = max(0, int(_RESEARCH_BUSY_ACTIVE) - 1)
        _RUNTIME_STATE.research_busy_active = int(_RESEARCH_BUSY_ACTIVE)
        if _RESEARCH_BUSY_ACTIVE <= 0:
            _RESEARCH_BUSY_HEARTBEAT_STOP.set()
            _RESEARCH_BUSY_HEARTBEAT_THREAD = None
            _clear_backend_busy()


def _compute_research_snapshot_job(
    market: str,
    kline: str,
    top_n: int,
    backtest_days: int,
    trace_id: str,
) -> dict[str, Any]:
    # 在独立计算进程内运行重负载研究，避免阻塞 API 主进程。
    with longport_history_priority(PRIORITY_LOW):
        return run_research_snapshot(
            trader=auto_trader,
            market=str(market),
            kline=str(kline),
            top_n=max(1, min(30, int(top_n))),
            backtest_days=max(90, min(365, int(backtest_days))),
            trace_id=str(trace_id or ""),
        )


def _compute_strategy_matrix_job(
    market: str,
    top_n: int,
    max_strategies: int,
    max_drawdown_limit_pct: float,
    min_symbols_used: int,
    trace_id: str,
    matrix_overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    with longport_history_priority(PRIORITY_LOW):
        return run_strategy_param_matrix(
            trader=auto_trader,
            market=str(market),
            top_n=max(1, min(30, int(top_n))),
            max_strategies=max(1, min(20, int(max_strategies))),
            max_drawdown_limit_pct=max(1.0, min(80.0, float(max_drawdown_limit_pct))),
            min_symbols_used=max(1, min(30, int(min_symbols_used))),
            trace_id=str(trace_id or ""),
            matrix_overrides=matrix_overrides if isinstance(matrix_overrides, dict) else None,
            cancel_checker=None,
        )


def _compute_ml_matrix_job(
    market: str,
    kline: str,
    top_n: int,
    signal_bars_days: int,
    trace_id: str,
    matrix_overrides: dict[str, Any] | None,
    constraints: dict[str, Any] | None,
    ranking_weights: dict[str, Any] | None,
) -> dict[str, Any]:
    with longport_history_priority(PRIORITY_LOW):
        return run_ml_param_matrix(
            trader=auto_trader,
            market=str(market),
            kline=str(kline),
            top_n=max(1, min(30, int(top_n))),
            signal_bars_days=max(120, min(365, int(signal_bars_days))),
            trace_id=str(trace_id or ""),
            matrix_overrides=matrix_overrides if isinstance(matrix_overrides, dict) else None,
            constraints=constraints if isinstance(constraints, dict) else None,
            ranking_weights=ranking_weights if isinstance(ranking_weights, dict) else None,
            cancel_checker=None,
        )


def _run_research_task(task_id: str, trace_id: str, market: str, kline: str, top_n: int, backtest_days: int) -> None:
    started = time.perf_counter()
    now = datetime.now().isoformat()
    row = _get_research_task(task_id) or {}
    row.update({"status": "running", "started_at": now, "trace_id": str(trace_id or "")})
    _set_task_progress(row, pct=8, stage="running", text="Research 任务已启动")
    _put_research_task(task_id, row)
    _research_busy_enter("research_async")
    try:
        def _is_cancelled() -> bool:
            cur = _get_research_task(task_id) or {}
            return str(cur.get("status", "")).lower() == "cancelled"

        if _is_cancelled():
            row.update({"status": "cancelled", "ended_at": datetime.now().isoformat()})
            _set_task_progress(row, pct=row.get("progress_pct", 8), stage="cancelled", text="任务已取消")
            _put_research_task(task_id, row)
            return

        snap: dict[str, Any]
        compute_executor = _get_research_process_executor()
        if compute_executor is not None:
            fut = compute_executor.submit(
                _compute_research_snapshot_job,
                str(market),
                str(kline),
                int(top_n),
                int(backtest_days),
                str(trace_id or ""),
            )
            cancel_marked = False
            while True:
                if _is_cancelled():
                    if fut.cancel():
                        row.update({"status": "cancelled", "ended_at": datetime.now().isoformat()})
                        _set_task_progress(row, pct=row.get("progress_pct", 10), stage="cancelled", text="任务已取消")
                        _put_research_task(task_id, row)
                        return
                    if not cancel_marked:
                        row.update({"status": "cancelling", "cancel_requested_at": datetime.now().isoformat()})
                        _set_task_progress(row, pct=row.get("progress_pct", 10), stage="cancelling", text="收到取消请求，等待计算中断")
                        _put_research_task(task_id, row)
                        cancel_marked = True
                try:
                    snap = fut.result(timeout=1.0)
                    break
                except FuturesTimeoutError:
                    _update_task_progress_by_elapsed(
                        row,
                        started_perf=started,
                        expected_seconds=180.0,
                        base_pct=10,
                        cap_pct=92,
                        stage="running",
                        text_prefix="Research 计算中",
                    )
                    _put_research_task(task_id, row)
                    continue
        else:
            _set_task_progress(row, pct=12, stage="running", text="Research 计算中")
            _put_research_task(task_id, row)
            snap = _compute_research_snapshot_job(
                market=str(market),
                kline=str(kline),
                top_n=int(top_n),
                backtest_days=int(backtest_days),
                trace_id=str(trace_id or ""),
            )
        row.update(
            {
                "status": "completed",
                "ended_at": datetime.now().isoformat(),
                "snapshot": {
                    "version": snap.get("version"),
                    "generated_at": snap.get("generated_at"),
                    "market": snap.get("market"),
                    "kline": snap.get("kline"),
                    "top_n": snap.get("top_n"),
                },
            }
        )
        _set_task_progress(row, pct=100, stage="completed", text="Research 任务完成")
        _put_research_task(task_id, row)
        emit_metric(
            event="api.auto_trader.research_run_async",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market), "kline": str(kline)},
            extra={
                "task_id": task_id,
                "trace_id": str(trace_id or ""),
                "top_n": int(top_n),
                "backtest_days": int(backtest_days),
            },
        )
    except Exception as e:
        row.update(
            {
                "status": "failed",
                "ended_at": datetime.now().isoformat(),
                "error": str(e),
            }
        )
        _set_task_progress(row, pct=row.get("progress_pct", 10), stage="failed", text=f"任务失败: {str(e)}")
        _put_research_task(task_id, row)
        emit_metric(
            event="api.auto_trader.research_run_async",
            ok=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market), "kline": str(kline)},
            extra={"task_id": task_id, "trace_id": str(trace_id or ""), "error": str(e)},
        )
    finally:
        _research_busy_leave()


def _run_strategy_matrix_task(
    task_id: str,
    trace_id: str,
    market: str,
    top_n: int,
    max_strategies: int,
    max_drawdown_limit_pct: float,
    min_symbols_used: int,
    matrix_overrides: Optional[dict[str, Any]],
) -> None:
    started = time.perf_counter()
    now = datetime.now().isoformat()
    row = _get_research_task(task_id) or {}
    row.update({"status": "running", "started_at": now, "trace_id": str(trace_id or "")})
    _set_task_progress(row, pct=8, stage="running", text="策略矩阵任务已启动")
    _put_research_task(task_id, row)
    _research_busy_enter("strategy_matrix_async")
    try:
        def _is_cancelled() -> bool:
            cur = _get_research_task(task_id) or {}
            return str(cur.get("status", "")).lower() == "cancelled"

        if _is_cancelled():
            row.update({"status": "cancelled", "ended_at": datetime.now().isoformat()})
            _set_task_progress(row, pct=row.get("progress_pct", 8), stage="cancelled", text="任务已取消")
            _put_research_task(task_id, row)
            return
        result: dict[str, Any]
        compute_executor = _get_research_process_executor()
        if compute_executor is not None:
            fut = compute_executor.submit(
                _compute_strategy_matrix_job,
                str(market),
                int(top_n),
                int(max_strategies),
                float(max_drawdown_limit_pct),
                int(min_symbols_used),
                str(trace_id or ""),
                matrix_overrides if isinstance(matrix_overrides, dict) else None,
            )
            cancel_marked = False
            while True:
                if _is_cancelled():
                    if fut.cancel():
                        row.update({"status": "cancelled", "ended_at": datetime.now().isoformat()})
                        _set_task_progress(row, pct=row.get("progress_pct", 10), stage="cancelled", text="任务已取消")
                        _put_research_task(task_id, row)
                        return
                    if not cancel_marked:
                        row.update({"status": "cancelling", "cancel_requested_at": datetime.now().isoformat()})
                        _set_task_progress(row, pct=row.get("progress_pct", 10), stage="cancelling", text="收到取消请求，等待计算中断")
                        _put_research_task(task_id, row)
                        cancel_marked = True
                try:
                    result = fut.result(timeout=1.0)
                    break
                except FuturesTimeoutError:
                    _update_task_progress_by_elapsed(
                        row,
                        started_perf=started,
                        expected_seconds=300.0,
                        base_pct=10,
                        cap_pct=92,
                        stage="running",
                        text_prefix="策略矩阵筛选中",
                    )
                    _put_research_task(task_id, row)
                    continue
        else:
            _set_task_progress(row, pct=12, stage="running", text="策略矩阵筛选中")
            _put_research_task(task_id, row)
            with longport_history_priority(PRIORITY_LOW):
                result = run_strategy_param_matrix(
                    trader=auto_trader,
                    market=str(market),
                    top_n=max(1, min(30, int(top_n))),
                    max_strategies=max(6, min(20, int(max_strategies))),
                    max_drawdown_limit_pct=max(1.0, min(80.0, float(max_drawdown_limit_pct))),
                    min_symbols_used=max(1, min(30, int(min_symbols_used))),
                    trace_id=str(trace_id or ""),
                    matrix_overrides=matrix_overrides if isinstance(matrix_overrides, dict) else None,
                    cancel_checker=_is_cancelled,
                )
        if bool(result.get("cancelled")):
            row.update(
                {
                    "status": "cancelled",
                    "ended_at": datetime.now().isoformat(),
                    "result_summary": {
                        "candidate_count": result.get("candidate_count"),
                    },
                }
            )
            _set_task_progress(row, pct=row.get("progress_pct", 40), stage="cancelled", text="任务已取消")
            _put_research_task(task_id, row)
            return
        row.update(
            {
                "status": "completed",
                "ended_at": datetime.now().isoformat(),
                "result_summary": {
                    "grid_size": result.get("grid_size"),
                    "strategy_count": result.get("strategy_count"),
                    "candidate_count": result.get("candidate_count"),
                    "best_balanced_strategy": (
                        (result.get("best_balanced") or {}).get("strategy_label")
                        or (result.get("best_balanced") or {}).get("strategy")
                    ),
                },
            }
        )
        _set_task_progress(row, pct=100, stage="completed", text="策略矩阵任务完成")
        _put_research_task(task_id, row)
        emit_metric(
            event="api.auto_trader.research_strategy_matrix_run_async",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market)},
            extra={"task_id": task_id, "trace_id": str(trace_id or ""), "top_n": int(top_n)},
        )
    except Exception as e:
        row.update(
            {
                "status": "failed",
                "ended_at": datetime.now().isoformat(),
                "error": str(e),
            }
        )
        _set_task_progress(row, pct=row.get("progress_pct", 10), stage="failed", text=f"任务失败: {str(e)}")
        _put_research_task(task_id, row)
        emit_metric(
            event="api.auto_trader.research_strategy_matrix_run_async",
            ok=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market)},
            extra={"task_id": task_id, "trace_id": str(trace_id or ""), "error": str(e)},
        )
    finally:
        _research_busy_leave()


def _run_ml_matrix_task(
    task_id: str,
    trace_id: str,
    market: str,
    kline: str,
    top_n: int,
    signal_bars_days: int,
    matrix_overrides: Optional[dict[str, Any]],
    constraints: Optional[dict[str, Any]],
    ranking_weights: Optional[dict[str, Any]],
) -> None:
    started = time.perf_counter()
    now = datetime.now().isoformat()
    row = _get_research_task(task_id) or {}
    row.update({"status": "running", "started_at": now, "trace_id": str(trace_id or "")})
    _set_task_progress(row, pct=8, stage="running", text="ML矩阵任务已启动")
    _put_research_task(task_id, row)
    _research_busy_enter("ml_matrix_async")
    try:
        def _is_cancelled() -> bool:
            cur = _get_research_task(task_id) or {}
            return str(cur.get("status", "")).lower() == "cancelled"

        if _is_cancelled():
            row.update({"status": "cancelled", "ended_at": datetime.now().isoformat()})
            _set_task_progress(row, pct=row.get("progress_pct", 8), stage="cancelled", text="任务已取消")
            _put_research_task(task_id, row)
            return
        result: dict[str, Any]
        compute_executor = _get_research_process_executor()
        if compute_executor is not None:
            fut = compute_executor.submit(
                _compute_ml_matrix_job,
                str(market),
                str(kline or "1d"),
                int(top_n),
                int(signal_bars_days),
                str(trace_id or ""),
                matrix_overrides if isinstance(matrix_overrides, dict) else None,
                constraints if isinstance(constraints, dict) else None,
                ranking_weights if isinstance(ranking_weights, dict) else None,
            )
            cancel_marked = False
            while True:
                if _is_cancelled():
                    if fut.cancel():
                        row.update({"status": "cancelled", "ended_at": datetime.now().isoformat()})
                        _set_task_progress(row, pct=row.get("progress_pct", 10), stage="cancelled", text="任务已取消")
                        _put_research_task(task_id, row)
                        return
                    if not cancel_marked:
                        row.update({"status": "cancelling", "cancel_requested_at": datetime.now().isoformat()})
                        _set_task_progress(row, pct=row.get("progress_pct", 10), stage="cancelling", text="收到取消请求，等待计算中断")
                        _put_research_task(task_id, row)
                        cancel_marked = True
                try:
                    result = fut.result(timeout=1.0)
                    break
                except FuturesTimeoutError:
                    _update_task_progress_by_elapsed(
                        row,
                        started_perf=started,
                        expected_seconds=420.0,
                        base_pct=10,
                        cap_pct=92,
                        stage="running",
                        text_prefix="ML矩阵筛选中",
                    )
                    _put_research_task(task_id, row)
                    continue
        else:
            _set_task_progress(row, pct=12, stage="running", text="ML矩阵筛选中")
            _put_research_task(task_id, row)
            with longport_history_priority(PRIORITY_LOW):
                result = run_ml_param_matrix(
                    trader=auto_trader,
                    market=str(market),
                    kline=str(kline or "1d"),
                    top_n=max(1, min(30, int(top_n))),
                    signal_bars_days=max(300, min(365, int(signal_bars_days))),
                    trace_id=str(trace_id or ""),
                    matrix_overrides=matrix_overrides if isinstance(matrix_overrides, dict) else None,
                    constraints=constraints if isinstance(constraints, dict) else None,
                    ranking_weights=ranking_weights if isinstance(ranking_weights, dict) else None,
                    cancel_checker=_is_cancelled,
                )
        if bool(result.get("cancelled")):
            row.update(
                {
                    "status": "cancelled",
                    "ended_at": datetime.now().isoformat(),
                    "result_summary": {
                        "evaluated_count": result.get("evaluated_count"),
                    },
                }
            )
            _set_task_progress(row, pct=row.get("progress_pct", 40), stage="cancelled", text="任务已取消")
            _put_research_task(task_id, row)
            return
        row.update(
            {
                "status": "completed",
                "ended_at": datetime.now().isoformat(),
                "result_summary": {
                    "grid_size": result.get("grid_size"),
                    "evaluated_count": result.get("evaluated_count"),
                    "passed_constraints_count": result.get("passed_constraints_count"),
                    "best_model_type": (((result.get("best_balanced") or {}).get("params") or {}).get("model_type")),
                },
            }
        )
        _set_task_progress(row, pct=100, stage="completed", text="ML矩阵任务完成")
        _put_research_task(task_id, row)
        emit_metric(
            event="api.auto_trader.research_ml_matrix_run_async",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market), "kline": str(kline or "1d")},
            extra={"task_id": task_id, "trace_id": str(trace_id or ""), "top_n": int(top_n)},
        )
    except Exception as e:
        row.update(
            {
                "status": "failed",
                "ended_at": datetime.now().isoformat(),
                "error": str(e),
            }
        )
        _set_task_progress(row, pct=row.get("progress_pct", 10), stage="failed", text=f"任务失败: {str(e)}")
        _put_research_task(task_id, row)
        emit_metric(
            event="api.auto_trader.research_ml_matrix_run_async",
            ok=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market), "kline": str(kline or "1d")},
            extra={"task_id": task_id, "trace_id": str(trace_id or ""), "error": str(e)},
        )
    finally:
        _research_busy_leave()


def _strong_stocks_response_attach_worker_scan_time(payload: dict[str, Any], worker_ts: Any) -> dict[str, Any]:
    """强势股接口可能走 API 即时筛选缓存；附带 Worker 最近一次完整扫描时间，便于与 decision_log 对齐排查。"""
    out = dict(payload)
    out["worker_last_scan_summary_at"] = worker_ts
    diag = out.get("diagnostics")
    if isinstance(diag, dict):
        out["diagnostics"] = {**diag, "worker_last_scan_summary_at": worker_ts}
    return out


def _strong_stocks_cache_key(market: str, limit: int, kline: str) -> str:
    return f"{str(market).lower()}::{int(limit)}::{str(kline).lower()}"


def _strong_stocks_cache_get(key: str, allow_stale: bool = False) -> Optional[dict[str, Any]]:
    now = time.time()
    ttl = _STRONG_STOCKS_CACHE_STALE_SECONDS if allow_stale else _STRONG_STOCKS_CACHE_TTL_SECONDS
    with _STRONG_STOCKS_CACHE_LOCK:
        row = _STRONG_STOCKS_CACHE.get(key)
        if not isinstance(row, dict):
            return None
        ts = float(row.get("ts", 0.0) or 0.0)
        if ts <= 0 or (now - ts) > float(ttl):
            return None
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return None
        return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def _strong_stocks_cache_put(key: str, payload: dict[str, Any]) -> None:
    with _STRONG_STOCKS_CACHE_LOCK:
        _STRONG_STOCKS_CACHE[key] = {"ts": time.time(), "payload": payload}
        if len(_STRONG_STOCKS_CACHE) > 100:
            keys = sorted(_STRONG_STOCKS_CACHE.keys(), key=lambda x: float(_STRONG_STOCKS_CACHE[x].get("ts", 0.0)))
            for k in keys[: max(0, len(_STRONG_STOCKS_CACHE) - 100)]:
                _STRONG_STOCKS_CACHE.pop(k, None)


def _refresh_strong_stocks_cache_job(cache_key: str, market: str, limit: int, kline: str) -> None:
    started = time.perf_counter()
    try:
        if not _STRONG_STOCKS_SEMAPHORE.acquire(timeout=0.1):
            return
        try:
            with longport_history_priority(PRIORITY_LOW):
                rows = auto_trader.screen_strong_stocks(market=market, limit=max(1, int(limit)), kline=str(kline))
        finally:
            _STRONG_STOCKS_SEMAPHORE.release()
        payload = {
            "market": market,
            "kline": kline,
            "count": min(len(rows), max(1, int(limit))),
            "items": rows[: max(1, int(limit))],
            "source": "api_live_fallback_async",
            "scan_time": datetime.now().isoformat(),
            "worker_running": bool(_auto_trader_runtime_status().get("worker_running")),
            "diagnostics": {
                "fallback_used": True,
                "cache_hit": False,
                "cache_stale": False,
                "rate_limited": False,
                "async_refresh": True,
            },
        }
        _strong_stocks_cache_put(cache_key, payload)
        emit_metric(
            event="api.auto_trader.strong_stocks_refresh",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": market, "kline": str(kline)},
            extra={"count": int(payload.get("count", 0))},
        )
    except Exception as e:
        emit_metric(
            event="api.auto_trader.strong_stocks_refresh",
            ok=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": market, "kline": str(kline)},
            extra={"error": str(e)},
        )
    finally:
        with _STRONG_STOCKS_REFRESH_LOCK:
            _STRONG_STOCKS_REFRESH_INFLIGHT.discard(cache_key)


def _schedule_strong_stocks_refresh(cache_key: str, market: str, limit: int, kline: str) -> bool:
    with _STRONG_STOCKS_REFRESH_LOCK:
        if cache_key in _STRONG_STOCKS_REFRESH_INFLIGHT:
            return False
        _STRONG_STOCKS_REFRESH_INFLIGHT.add(cache_key)
    _STRONG_STOCKS_REFRESH_EXECUTOR.submit(_refresh_strong_stocks_cache_job, cache_key, market, max(1, int(limit)), str(kline))
    return True


def auto_trader_metrics_recent(limit: int = 200, event: Optional[str] = None) -> dict[str, Any]:
    rows = read_recent_metrics(limit=limit, event=event)
    return {"count": len(rows), "limit": max(1, min(2000, int(limit))), "event": event, "items": rows}


def auto_trader_metrics_sla(window_minutes: int = 5, limit: int = 2000) -> dict[str, Any]:
    wm = max(1, min(120, int(window_minutes)))
    rows = read_recent_metrics(limit=max(200, min(4000, int(limit))))
    cutoff = time.time() - wm * 60
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = str(row.get("ts", ""))
        try:
            ts_epoch = datetime.fromisoformat(ts).timestamp()
        except Exception:
            continue
        if ts_epoch < cutoff:
            continue
        event = str(row.get("event", ""))
        if not event.startswith("api.auto_trader."):
            continue
        filtered.append(row)

    def _calc(group_rows: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(group_rows)
        errors = sum(1 for x in group_rows if not bool(x.get("ok")))
        lat = sorted(
            [float(x.get("elapsed_ms")) for x in group_rows if isinstance(x.get("elapsed_ms"), (int, float)) and float(x.get("elapsed_ms")) >= 0]
        )
        p95 = lat[int(0.95 * (len(lat) - 1))] if lat else 0.0
        conn_errors = 0
        for x in group_rows:
            extra = x.get("extra")
            err = str(extra.get("error", "")) if isinstance(extra, dict) else ""
            err_low = err.lower()
            if "connection" in err_low or "10054" in err or "10061" in err:
                conn_errors += 1
        return {
            "total": total,
            "errors": errors,
            "error_rate_pct": round((errors / total * 100.0), 3) if total else 0.0,
            "p95_ms": round(float(p95), 3),
            "connection_errors": conn_errors,
        }

    by_market: dict[str, list[dict[str, Any]]] = {"us": [], "hk": [], "cn": [], "unknown": []}
    for row in filtered:
        tags = row.get("tags")
        market = str(tags.get("market", "unknown")).lower() if isinstance(tags, dict) else "unknown"
        if market not in by_market:
            market = "unknown"
        by_market[market].append(row)

    return {
        "window_minutes": wm,
        "overall": _calc(filtered),
        "markets": {k: _calc(v) for k, v in by_market.items()},
    }


@app.get("/ops/restarts/recent")
def ops_restarts_recent(limit: int = 20) -> dict[str, Any]:
    rows = _read_watchdog_restart_events(limit=limit)
    return {"count": len(rows), "limit": max(1, min(500, int(limit))), "items": rows}


@app.get("/research/external/openbb/health")
def research_external_openbb_health() -> dict[str, Any]:
    cli = OpenBBClient()
    return {"ok": True, "provider": "openbb", "health": cli.health()}


@app.get("/research/external/openbb/market-regime")
def research_external_openbb_market_regime(market: str = "us") -> dict[str, Any]:
    cli = OpenBBClient()
    row = cli.market_regime(market=market)
    return {"ok": True, "provider": "openbb", "item": row}


@app.get("/research/external/openbb/symbol-factor")
def research_external_openbb_symbol_factor(symbol: str, market: str = "us", kline: str = "1d") -> dict[str, Any]:
    cli = OpenBBClient()
    row = cli.symbol_factor(symbol=symbol, market=market, kline=kline)
    return {"ok": True, "provider": "openbb", "item": row}


def auto_trader_research_status() -> dict[str, Any]:
    base = get_research_status()
    rows = _list_research_tasks()
    queued_positions = _queued_task_positions(rows)
    queued = 0
    running = 0
    active_tasks: list[dict[str, Any]] = []
    queued_by_type = {"research": 0, "strategy_matrix": 0, "ml_matrix": 0, "unknown": 0}
    running_by_type = {"research": 0, "strategy_matrix": 0, "ml_matrix": 0, "unknown": 0}
    for row in rows:
        status = str(row.get("status", "")).lower()
        task_type = str(row.get("task_type", "unknown") or "unknown").lower()
        if task_type not in {"research", "strategy_matrix", "ml_matrix"}:
            task_type = "unknown"
        if status == "queued":
            queued += 1
            queued_by_type[task_type] += 1
            task_id = str(row.get("task_id") or "")
            queue_position = int(queued_positions.get(task_id, 0) or 0)
            active_tasks.append(
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "status": status,
                    "created_at": row.get("created_at"),
                    "started_at": row.get("started_at"),
                    "progress_pct": _clamp_progress_pct(row.get("progress_pct", 0)),
                    "progress_stage": str(row.get("progress_stage") or "queued"),
                    "progress_text": str(row.get("progress_text") or "任务排队中"),
                    "progress_updated_at": row.get("progress_updated_at"),
                    "queue_position": queue_position,
                    "queue_ahead": max(0, queue_position - 1) if queue_position else 0,
                }
            )
        elif status == "running":
            running += 1
            running_by_type[task_type] += 1
            active_tasks.append(
                {
                    "task_id": str(row.get("task_id") or ""),
                    "task_type": task_type,
                    "status": status,
                    "created_at": row.get("created_at"),
                    "started_at": row.get("started_at"),
                    "progress_pct": _clamp_progress_pct(row.get("progress_pct", 10)),
                    "progress_stage": str(row.get("progress_stage") or "running"),
                    "progress_text": str(row.get("progress_text") or "任务运行中"),
                    "progress_updated_at": row.get("progress_updated_at"),
                    "queue_position": 0,
                    "queue_ahead": 0,
                }
            )
    active_tasks.sort(
        key=lambda x: (
            0 if str(x.get("status", "")).lower() == "running" else 1,
            str(x.get("started_at") or x.get("created_at") or ""),
        )
    )
    base["task_queue"] = {
        "queued": queued,
        "running": running,
        "active": queued + running,
        "max_pending": _RESEARCH_TASK_MAX_PENDING,
        "queued_by_type": queued_by_type,
        "running_by_type": running_by_type,
        "active_tasks": active_tasks,
    }
    return base


def auto_trader_research_snapshot() -> dict[str, Any]:
    snap = get_research_snapshot()
    if not snap:
        return {"has_snapshot": False, "items": []}
    return {"has_snapshot": True, "snapshot": snap}


def auto_trader_research_snapshot_history_list(
    history_type: str,
    market: Optional[str] = Query(None),
) -> dict[str, Any]:
    return list_research_snapshot_history(market=market, history_type=history_type)


def auto_trader_research_snapshot_history_get(
    history_type: str,
    snapshot_id: str,
    market: Optional[str] = Query(None),
) -> dict[str, Any]:
    raw = get_research_snapshot_history_result(
        market=market,
        history_type=history_type,
        snapshot_id=snapshot_id,
    )
    if not raw:
        raise HTTPException(
            status_code=404,
            detail={"error": "snapshot_history_not_found", "history_type": history_type, "snapshot_id": snapshot_id},
        )
    return raw


def auto_trader_research_run(body: Optional[AutoTraderResearchRunBody] = None) -> dict[str, Any]:
    started = time.perf_counter()
    trace_id = f"trace-{uuid4().hex[:12]}"
    cfg = auto_trader.get_config()
    market = (body.market if body and body.market else cfg.get("market", "us")) or "us"
    kline = (body.kline if body and body.kline else cfg.get("kline", "1d")) or "1d"
    top_n = int(body.top_n if body and body.top_n is not None else cfg.get("top_n", 8) or 8)
    backtest_days = int(
        body.backtest_days if body and body.backtest_days is not None else cfg.get("backtest_days", 180) or 180
    )
    async_run = bool(body.async_run) if body else False
    if async_run:
        if _count_active_research_tasks() >= _RESEARCH_TASK_MAX_PENDING:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "too_many_pending_tasks",
                    "max_pending": _RESEARCH_TASK_MAX_PENDING,
                },
            )
        dedupe = _find_duplicate_async_task(
            task_type="research",
            params_subset={
                "market": str(market),
                "kline": str(kline),
                "top_n": max(1, top_n),
                "backtest_days": max(90, backtest_days),
            },
        )
        if isinstance(dedupe, dict):
            return {
                "ok": True,
                "accepted": False,
                "mode": "async",
                "task_id": dedupe.get("task_id"),
                "trace_id": dedupe.get("trace_id"),
                "message": "duplicate_task_reused",
            }
        task_id = f"rs-{uuid4().hex[:12]}"
        row = {
            "task_id": task_id,
            "trace_id": trace_id,
            "task_type": "research",
            "status": "queued",
            "created_at": datetime.now().isoformat(),
            "progress_pct": 0,
            "progress_stage": "queued",
            "progress_text": "任务排队中",
            "progress_updated_at": datetime.now().isoformat(),
            "params": {
                "market": str(market),
                "kline": str(kline),
                "top_n": max(1, top_n),
                "backtest_days": max(90, backtest_days),
            },
        }
        _put_research_task(task_id, row)
        _RESEARCH_EXECUTOR.submit(
            _run_research_task,
            task_id,
            trace_id,
            str(market),
            str(kline),
            max(1, top_n),
            max(90, backtest_days),
        )
        emit_metric(
            event="api.auto_trader.research_run",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"mode": "async", "market": str(market), "kline": str(kline)},
            extra={"task_id": task_id, "trace_id": trace_id},
        )
        return {"ok": True, "accepted": True, "mode": "async", "task_id": task_id, "trace_id": trace_id}

    try:
        _research_busy_enter("research_sync")
        with longport_history_priority(PRIORITY_LOW):
            snap = run_research_snapshot(
                trader=auto_trader,
                market=str(market),
                kline=str(kline),
                top_n=max(1, top_n),
                backtest_days=max(90, backtest_days),
                trace_id=trace_id,
            )
        emit_metric(
            event="api.auto_trader.research_run",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market), "kline": str(kline)},
            extra={"trace_id": trace_id, "top_n": max(1, top_n), "backtest_days": max(90, backtest_days)},
        )
        return {"ok": True, "trace_id": trace_id, "snapshot": snap}
    except Exception as e:
        emit_metric(
            event="api.auto_trader.research_run",
            ok=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market), "kline": str(kline)},
            extra={"trace_id": trace_id, "error": str(e)},
        )
        raise
    finally:
        _research_busy_leave()


def auto_trader_research_task_status(task_id: str) -> dict[str, Any]:
    row = _get_research_task(task_id)
    if not row:
        raise HTTPException(status_code=404, detail={"error": "task_not_found", "task_id": task_id})
    if str(row.get("status", "")).lower() == "queued":
        rows = _list_research_tasks()
        queued_positions = _queued_task_positions(rows)
        qp = int(queued_positions.get(str(task_id), 0) or 0)
        row["queue_position"] = qp
        row["queue_ahead"] = max(0, qp - 1) if qp else 0
    return row


def auto_trader_research_task_cancel(task_id: str) -> dict[str, Any]:
    row = _get_research_task(task_id)
    if not row:
        raise HTTPException(status_code=404, detail={"error": "task_not_found", "task_id": task_id})
    status = str(row.get("status", "")).lower()
    if status in {"completed", "failed", "cancelled"}:
        return {"ok": True, "task_id": task_id, "status": status, "message": "task_already_finished"}
    row["status"] = "cancelled"
    row["cancelled_at"] = datetime.now().isoformat()
    _set_task_progress(
        row,
        pct=row.get("progress_pct", 0),
        stage="cancelled",
        text="任务已取消",
    )
    _put_research_task(task_id, row)
    return {"ok": True, "task_id": task_id, "status": "cancelled"}


def auto_trader_research_model_compare(top: int = 10) -> dict[str, Any]:
    return get_model_compare(top=max(1, min(50, int(top))))


def auto_trader_research_strategy_matrix_run(
    body: Optional[AutoTraderStrategyMatrixRunBody] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    trace_id = f"trace-{uuid4().hex[:12]}"
    cfg = auto_trader.get_config()
    market = (body.market if body and body.market else cfg.get("market", "us")) or "us"
    top_n = int(body.top_n if body and body.top_n is not None else cfg.get("top_n", 8) or 8)
    max_strategies = int(body.max_strategies if body and body.max_strategies is not None else 4)
    max_drawdown_limit_pct = float(
        body.max_drawdown_limit_pct if body and body.max_drawdown_limit_pct is not None else 30.0
    )
    min_symbols_used = int(body.min_symbols_used if body and body.min_symbols_used is not None else 3)
    matrix_overrides = body.matrix_overrides if body and isinstance(body.matrix_overrides, dict) else None
    async_run = bool(body.async_run) if body else True
    _sm_pool_mode = "config_subset" if bool((matrix_overrides or {}).get("use_config_strategies_only")) else "all_registered"
    _sm_task_params = _strategy_matrix_task_params(
        cfg,
        market=str(market),
        top_n=int(top_n),
        max_strategies=int(max_strategies),
        max_drawdown_limit_pct=float(max_drawdown_limit_pct),
        min_symbols_used=int(min_symbols_used),
        strategy_pool_mode=_sm_pool_mode,
        matrix_overrides=matrix_overrides,
    )

    if async_run:
        if _count_active_research_tasks() >= _RESEARCH_TASK_MAX_PENDING:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "too_many_pending_tasks",
                    "max_pending": _RESEARCH_TASK_MAX_PENDING,
                },
            )
        dedupe = _find_duplicate_async_task(
            task_type="strategy_matrix",
            params_subset=_sm_task_params,
        )
        if isinstance(dedupe, dict):
            return {
                "ok": True,
                "accepted": False,
                "mode": "async",
                "task_id": dedupe.get("task_id"),
                "trace_id": dedupe.get("trace_id"),
                "message": "duplicate_task_reused",
            }
        task_id = f"sm-{uuid4().hex[:12]}"
        row = {
            "task_id": task_id,
            "trace_id": trace_id,
            "task_type": "strategy_matrix",
            "status": "queued",
            "created_at": datetime.now().isoformat(),
            "progress_pct": 0,
            "progress_stage": "queued",
            "progress_text": "任务排队中",
            "progress_updated_at": datetime.now().isoformat(),
            "params": dict(_sm_task_params),
        }
        _put_research_task(task_id, row)
        _RESEARCH_EXECUTOR.submit(
            _run_strategy_matrix_task,
            task_id,
            trace_id,
            str(market),
            max(8, min(30, top_n)),
            max(6, min(20, max_strategies)),
            max(1.0, min(80.0, max_drawdown_limit_pct)),
            max(3, min(30, min_symbols_used)),
            matrix_overrides,
        )
        emit_metric(
            event="api.auto_trader.research_strategy_matrix_run",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"mode": "async", "market": str(market)},
            extra={"task_id": task_id, "trace_id": trace_id},
        )
        return {"ok": True, "accepted": True, "mode": "async", "task_id": task_id, "trace_id": trace_id}

    try:
        _research_busy_enter("strategy_matrix")
        with longport_history_priority(PRIORITY_LOW):
            result = run_strategy_param_matrix(
                trader=auto_trader,
                market=str(market),
                top_n=max(8, min(30, top_n)),
                max_strategies=max(6, min(20, max_strategies)),
                max_drawdown_limit_pct=max(1.0, min(80.0, max_drawdown_limit_pct)),
                min_symbols_used=max(3, min(30, min_symbols_used)),
                trace_id=trace_id,
                matrix_overrides=matrix_overrides,
            )
        emit_metric(
            event="api.auto_trader.research_strategy_matrix_run",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"mode": "sync", "market": str(market)},
            extra={"trace_id": trace_id, "top_n": max(1, min(30, top_n))},
        )
        return {"ok": True, "trace_id": trace_id, "result": result}
    except Exception as e:
        emit_metric(
            event="api.auto_trader.research_strategy_matrix_run",
            ok=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market)},
            extra={"trace_id": trace_id, "error": str(e)},
        )
        raise
    finally:
        _research_busy_leave()


def auto_trader_research_strategy_matrix_result(market: Optional[str] = Query(None)) -> dict[str, Any]:
    cfg = auto_trader.get_config() if hasattr(auto_trader, "get_config") else {}
    m = str(market or (cfg.get("market") if isinstance(cfg, dict) else None) or "us")
    row = get_strategy_param_matrix_result(m)
    return {"has_result": bool(row), "result": row if row else {}, "market": m}


def auto_trader_research_ml_matrix_run(body: Optional[AutoTraderMlMatrixRunBody] = None) -> dict[str, Any]:
    started = time.perf_counter()
    trace_id = f"trace-{uuid4().hex[:12]}"
    cfg = auto_trader.get_config()
    market = (body.market if body and body.market else cfg.get("market", "us")) or "us"
    kline = (body.kline if body and body.kline else cfg.get("kline", "1d")) or "1d"
    top_n = int(body.top_n if body and body.top_n is not None else cfg.get("top_n", 8) or 8)
    signal_bars_days = int(
        body.signal_bars_days if body and body.signal_bars_days is not None else cfg.get("signal_bars_days", 300) or 300
    )
    matrix_overrides = body.matrix_overrides if body and isinstance(body.matrix_overrides, dict) else None
    constraints = body.constraints if body and isinstance(body.constraints, dict) else None
    ranking_weights = body.ranking_weights if body and isinstance(body.ranking_weights, dict) else None
    async_run = bool(body.async_run) if body else True

    if async_run:
        if _count_active_research_tasks() >= _RESEARCH_TASK_MAX_PENDING:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "too_many_pending_tasks",
                    "max_pending": _RESEARCH_TASK_MAX_PENDING,
                },
            )
        dedupe = _find_duplicate_async_task(
            task_type="ml_matrix",
            params_subset={
                "market": str(market),
                "kline": str(kline),
                "top_n": max(1, min(30, top_n)),
                "signal_bars_days": max(300, min(365, signal_bars_days)),
            },
        )
        if isinstance(dedupe, dict):
            return {
                "ok": True,
                "accepted": False,
                "mode": "async",
                "task_id": dedupe.get("task_id"),
                "trace_id": dedupe.get("trace_id"),
                "message": "duplicate_task_reused",
            }
        task_id = f"mm-{uuid4().hex[:12]}"
        row = {
            "task_id": task_id,
            "trace_id": trace_id,
            "task_type": "ml_matrix",
            "status": "queued",
            "created_at": datetime.now().isoformat(),
            "progress_pct": 0,
            "progress_stage": "queued",
            "progress_text": "任务排队中",
            "progress_updated_at": datetime.now().isoformat(),
            "params": {
                "market": str(market),
                "kline": str(kline),
                "top_n": max(1, min(30, top_n)),
                "signal_bars_days": max(300, min(365, signal_bars_days)),
            },
        }
        _put_research_task(task_id, row)
        _RESEARCH_EXECUTOR.submit(
            _run_ml_matrix_task,
            task_id,
            trace_id,
            str(market),
            str(kline),
            max(1, min(30, top_n)),
            max(300, min(365, signal_bars_days)),
            matrix_overrides,
            constraints,
            ranking_weights,
        )
        emit_metric(
            event="api.auto_trader.research_ml_matrix_run",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"mode": "async", "market": str(market), "kline": str(kline)},
            extra={"task_id": task_id, "trace_id": trace_id},
        )
        return {"ok": True, "accepted": True, "mode": "async", "task_id": task_id, "trace_id": trace_id}

    try:
        _research_busy_enter("ml_matrix")
        with longport_history_priority(PRIORITY_LOW):
            result = run_ml_param_matrix(
                trader=auto_trader,
                market=str(market),
                kline=str(kline),
                top_n=max(1, min(30, top_n)),
                signal_bars_days=max(300, min(365, signal_bars_days)),
                trace_id=trace_id,
                matrix_overrides=matrix_overrides,
                constraints=constraints,
                ranking_weights=ranking_weights,
            )
        emit_metric(
            event="api.auto_trader.research_ml_matrix_run",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"mode": "sync", "market": str(market), "kline": str(kline)},
            extra={"trace_id": trace_id, "top_n": max(1, min(30, top_n))},
        )
        return {"ok": True, "trace_id": trace_id, "result": result}
    except Exception as e:
        emit_metric(
            event="api.auto_trader.research_ml_matrix_run",
            ok=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(market), "kline": str(kline)},
            extra={"trace_id": trace_id, "error": str(e)},
        )
        raise
    finally:
        _research_busy_leave()


def auto_trader_research_ml_matrix_result(market: Optional[str] = Query(None)) -> dict[str, Any]:
    cfg = auto_trader.get_config() if hasattr(auto_trader, "get_config") else {}
    m = str(market or (cfg.get("market") if isinstance(cfg, dict) else None) or "us")
    row = get_ml_param_matrix_result(m)
    return {"has_result": bool(row), "result": row if row else {}, "market": m}


def auto_trader_research_ml_matrix_apply_to_config(
    body: Optional[AutoTraderMlMatrixApplyBody] = None,
) -> dict[str, Any]:
    """一键将 ML 矩阵选中的最优（或指定 variant）参数合并到 auto_trader_config.json。"""
    cfg0 = auto_trader.get_config() if hasattr(auto_trader, "get_config") else {}
    mkt = str((cfg0.get("market") if isinstance(cfg0, dict) else None) or "us")
    snapshot_id = str(getattr(body, "snapshot_id", "") or "").strip() if body else ""
    if snapshot_id:
        raw = get_research_snapshot_history_result(
            market=mkt,
            history_type="ml_matrix",
            snapshot_id=snapshot_id,
        )
    else:
        raw = get_ml_param_matrix_result(mkt)
    if not isinstance(raw, dict) or not raw:
        raise HTTPException(status_code=400, detail="no_ml_matrix_result_run_matrix_first")
    if not bool(raw.get("ok")):
        err = str(raw.get("error") or "ml_matrix_not_ok")
        raise HTTPException(status_code=400, detail={"error": err, "message": "ML矩阵上次运行未成功，无法应用"})

    variant = (body.variant if body else "auto") or "auto"
    enable_ml = bool(body.enable_ml_filter) if body else True
    picked, source = resolve_ml_matrix_row_for_apply(raw, variant)
    if not picked or not isinstance(picked.get("params"), dict):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "no_applicable_ml_matrix_row",
                "message": "没有可选中的矩阵行（可先放宽约束或改用 variant=best_score）",
                "variant": variant,
            },
        )

    patch = ml_matrix_row_to_auto_trader_patch(picked)
    if not patch:
        raise HTTPException(status_code=400, detail="invalid_ml_matrix_params")

    payload: dict[str, Any] = {**patch, "ml_filter_enabled": bool(enable_ml)}
    cfg = auto_trader.update_config(payload)
    worker_sync = _sync_auto_trader_worker_with_config(cfg)
    emit_metric(
        event="api.auto_trader.research_ml_matrix_apply_to_config",
        ok=True,
        tags={"variant": str(variant), "source": str(source)},
        extra={"ml_model_type": patch.get("ml_model_type")},
    )
    return {
        "ok": True,
        "applied_from": source,
        "variant": variant,
        "ml_filter_enabled": bool(enable_ml),
        "patch": patch,
        "picked_summary": {
            "score": picked.get("score"),
            "pass_constraints": picked.get("pass_constraints"),
            "metrics": picked.get("metrics"),
        },
        "config": cfg,
        "message": "已合并 ML 参数到自动交易配置；Worker 会从配置文件读取更新（若进程未运行，保存配置时会自动尝试拉起）。",
        **worker_sync,
    }


def auto_trader_research_ab_report() -> dict[str, Any]:
    row = get_factor_ab_report()
    return {"has_report": bool(row), "report": row if row else {}}


def auto_trader_research_ab_report_markdown() -> dict[str, Any]:
    return get_factor_ab_report_markdown()


@app.get("/internal/longport/quote")
def internal_longport_quote(symbol: str) -> dict[str, Any]:
    gw = _gateway_get_json("/internal/longport/quote", {"symbol": symbol})
    if isinstance(gw, dict) and ("available" in gw):
        return gw

    qctx, _ = ensure_contexts()
    sym = str(symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol_required")
    qs = qctx.quote([sym])
    if not qs:
        return {"symbol": sym, "available": False, "reason": "quote_empty"}
    q = qs[0]
    last, price_type = _get_realtime_price(q)
    prev = float(getattr(q, "prev_close", 0.0) or 0.0)
    chg = ((last - prev) / prev * 100) if prev else 0.0
    return {
        "symbol": sym,
        "available": True,
        "last": float(last),
        "change_pct": round(float(chg), 2),
        "price_type": str(price_type),
        "prev_close": float(prev),
    }


@app.get("/internal/longport/history-bars")
def internal_longport_history_bars(
    symbol: str,
    days: int = 120,
    kline: BacktestKline = "1d",
    priority: Optional[str] = None,
) -> dict[str, Any]:
    with using_priority_param(priority):
        sym = str(symbol or "").strip().upper()
        if not sym:
            raise HTTPException(status_code=400, detail="symbol_required")
        ds = max(10, min(3650, int(days)))
        need_est = _estimate_bars_upper_bound_calendar(ds, str(kline))

        gw = _gateway_get_json(
            "/internal/longport/history-bars", {"symbol": sym, "days": int(ds), "kline": str(kline)}
        )
        if isinstance(gw, dict) and isinstance(gw.get("items"), list):
            items = gw.get("items") or []
            # 网关/单次接口常见「顶满 ~1000 根」截断：条数卡在千根附近且估算窗口需要更多根时，改用本地分页对齐拉取
            suspicious_truncation = 999 <= len(items) <= 1010 and need_est > 1100
            if items and not suspicious_truncation:
                return gw

        try:
            bars = _fetch_bars_calendar_days(sym, ds, kline, _skip_gateway=True)
        except Exception as e:
            if _is_longport_connect_error(e):
                throttled_reset_contexts(reset_contexts, _RUNTIME_STATE)
                bars = []
            else:
                raise
        return {
            "symbol": sym,
            "kline": str(kline),
            "days": ds,
            "available": bool(bars),
            "count": len(bars),
            "items": [
                {
                    "date": b.date.isoformat() if hasattr(b.date, "isoformat") else str(b.date),
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                    "volume": float(b.volume),
                }
                for b in bars
            ],
        }


app.include_router(setup_router)
app.include_router(auto_trader_router)
app.include_router(notifications_router)
app.include_router(fees_risk_router)
app.include_router(options_trade_router)
app.include_router(dashboard_market_router)
app.include_router(backtest_router)

