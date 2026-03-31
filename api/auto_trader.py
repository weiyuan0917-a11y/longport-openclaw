from __future__ import annotations

import json
import logging
import os
import threading
import time
import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone, time as dt_time
from typing import Any, Callable, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from api.engine import (
    BreakoutRule,
    FixedSizer,
    HardStopRule,
    MeanReversionRule,
    PositionSnapshot,
    RiskPercentSizer,
    ScanContext,
    StrategyCrossRule,
    StrategyPipeline,
    StrategySellRule,
    TakeProfitRule,
    TimeStopRule,
    VolatilitySizer,
)
from api.engine.guards import DailyTradeLimitGuard, ExistingPositionGuard, SymbolCooldownGuard
from mcp_server.backtest_engine import BacktestEngine
from mcp_server.ml_common import (
    FEATURE_COLUMNS,
    build_ml_feature_frame,
    create_ml_classifier,
    walk_forward_probability_map,
)
from mcp_server.risk_manager import trade_value
from mcp_server.strategies import get_strategy, list_strategy_names
from config.notification_settings import resolve_feishu_app_config
from api.etf_pair_portfolio import (
    DEFAULT_PAIR_POOL,
    flatten_pair_symbols,
    normalize_pair_pool,
    run_pair_portfolio_backtest,
)
from api.perf_metrics import emit_metric

logger = logging.getLogger(__name__)


def _strategy_spec_key(strategy_name: str, params: Optional[dict[str, Any]]) -> str:
    try:
        blob = json.dumps({"s": strategy_name, "p": params or {}}, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        blob = f"{strategy_name}|{params!r}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _strategy_params_for_scan_context(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    sp = row.get("strategy_params")
    if not isinstance(sp, dict) or not sp:
        return None
    return dict(sp)


def make_feishu_sender(config_path: str) -> Callable[[str], bool]:
    """创建飞书消息发送函数"""
    def send_feishu(text: str) -> bool:
        cfg = resolve_feishu_app_config(config_path)
        try:
            # 每次发送都从配置文件重新加载，避免进程长驻导致 webhook/secret 变更不生效。
            from mcp_server.feishu_bot import load_notification_config
            mgr = load_notification_config()
            results = mgr.send_text(text)
            if any(results.values()):
                return True
        except Exception:
            pass

        # webhook 失败时，回退到飞书应用 chat_id 发送（与定时市场报告链路一致）。
        app_id = str(cfg.get("app_id") or "").strip()
        app_secret = str(cfg.get("app_secret") or "").strip()
        scheduled_chat_id = str(cfg.get("scheduled_chat_id") or "").strip()
        if not (app_id and app_secret and scheduled_chat_id):
            return False
        try:
            import json as _json
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            client = (
                lark.Client.builder()
                .app_id(app_id)
                .app_secret(app_secret)
                .build()
            )
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(scheduled_chat_id)
                    .msg_type("text")
                    .content(_json.dumps({"text": text}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            resp = client.im.v1.message.create(req)
            return bool(resp.success())
        except Exception:
            return False
    return send_feishu


# ============================================================
# 半自动 pending 信号落盘 JSON（API 与 Worker 对齐）
# ============================================================
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_TRADER_SIGNALS_PERSIST_FILE = os.path.join(_ROOT, ".auto_trader_signals.json")
AUTO_TRADER_SIGNALS_PERSIST_MAX = max(1, int(os.getenv("AUTO_TRADER_SIGNALS_PERSIST_MAX", "500")))
AUTO_TRADER_SCAN_COUNTER_FILE = os.path.join(_ROOT, ".auto_trader_scan_counter.json")
_ET = ZoneInfo("America/New_York")
_BJ = ZoneInfo("Asia/Shanghai")


def _safe_parse_iso_datetime(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except Exception:
        return None


def load_persisted_signals(status: str = "all") -> list[dict[str, Any]]:
    """
    读取 Worker 侧落盘信号，供 API `/auto-trader/signals` 合并返回。
    - status='pending'：过滤已过期 pending
    - status='executed'：包含 executed/simulated
    """
    try:
        if not os.path.exists(AUTO_TRADER_SIGNALS_PERSIST_FILE):
            return []
        raw = open(AUTO_TRADER_SIGNALS_PERSIST_FILE, "r", encoding="utf-8").read()
        if not raw.strip():
            return []
        data = json.loads(raw)
        if not isinstance(data, dict):
            return []
        signals = data.get("signals", [])
        if not isinstance(signals, list):
            return []
    except Exception:
        return []

    now = datetime.now()
    rows: list[dict[str, Any]] = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        st = str(s.get("status", "") or "")
        if status == "all":
            rows.append(s)
            continue
        if status == "executed":
            if st in {"executed", "simulated"}:
                rows.append(s)
            continue
        if status == "pending":
            exp = _safe_parse_iso_datetime(s.get("expires_at"))
            if st == "pending" and (exp is None or now <= exp):
                rows.append(s)
            continue
        if st == status:
            rows.append(s)

    def _sort_key(x: dict[str, Any]) -> str:
        # created_at isoformat 可直接按字符串反向排序
        return str(x.get("created_at") or x.get("updated_at") or "")

    return sorted(rows, key=_sort_key, reverse=True)

class AutoTraderService:
    """全自动交易服务: screen -> score -> signal -> execute (with risk control)."""

    def __init__(
        self,
        fetch_bars: Callable[[str, int, str], list[Any]],
        quote_last: Callable[[str], Optional[dict[str, float]]],
        send_feishu: Callable[[str], bool],
        execute_trade: Callable[[str, str, int, float], dict[str, Any]],
        get_positions: Callable[[], dict[str, Any]],
        get_account: Callable[[], dict[str, Any]],
        config_path: Optional[str] = None,
    ):
        self._fetch_bars = fetch_bars
        self._quote_last = quote_last
        self._send_feishu = send_feishu
        self._execute_trade = execute_trade
        self._get_positions = get_positions
        self._get_account = get_account
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._config_path = config_path or os.path.join(os.path.dirname(__file__), "auto_trader_config.json")
        self._backup_path = self._config_path.replace(".json", ".backups.json")
        self._max_backups = 10

        self._default_config: dict[str, Any] = {
            "enabled": False,
            "market": "us",
            "active_template": "custom",
            "pair_mode": False,
            "pair_mode_allow_auto_execute": False,  # 高级开关：允许ETF配对模式下全自动执行
            "interval_seconds": 300,
            "top_n": 8,
            "kline": "1d",
            "backtest_days": 120,
            "signal_bars_days": 90,
            "order_quantity": 100,
            "dry_run_mode": False,  # 只读演练：生成信号但不下单
            "entry_rule": "strategy_cross",
            "breakout_lookback_bars": 20,
            "breakout_volume_ratio": 1.2,
            "mean_reversion_rsi_threshold": 35.0,
            "mean_reversion_deviation_pct": 2.0,
            "exit_rules": ["hard_stop", "take_profit", "strategy_sell"],
            "rule_priority": ["hard_stop", "take_profit", "strategy_sell", "time_stop"],
            "hard_stop_pct": 6.0,
            "take_profit_pct": 12.0,
            "time_stop_hours": 72,
            "sizer": {"type": "fixed", "quantity": 100, "risk_pct": 0.01, "target_vol_pct": 0.02},
            "cost_model": {"commission_bps": 3, "slippage_bps": 5},
            "auto_execute": True,  # 全自动执行开关
            "auto_sell_enabled": False,  # 自动卖出开关（默认关闭，避免误触发）
            "sell_full_position": True,  # 卖出时默认清仓
            "sell_order_quantity": 100,  # 非清仓模式下每次卖出股数
            "signal_relaxed_mode": False,  # False=仅新触发信号；True=当前为buy即可触发
            "auto_prune_invalid_symbols": True,  # 自动剔除无效代码
            "observer_mode_enabled": True,  # 观察模式提示开关
            "observer_no_signal_rounds": 3,  # 连续N轮无信号后提醒
            "same_symbol_cooldown_minutes": 30,  # 同标的连续下单冷却
            "same_symbol_max_trades_per_day": 1,  # 同标的当日最多自动下单次数
            "same_symbol_max_sells_per_day": 1,  # 同标的当日最多自动卖出次数
            "avoid_add_to_existing_position": True,  # 已有持仓时不自动加仓
            "max_daily_trades": 5,  # 每日最大交易次数
            "daily_loss_circuit_enabled": True,  # 日损失熔断开关
            "daily_loss_limit_pct": 0.03,  # 日损失熔断阈值（相对日初权益）
            "consecutive_loss_stop_enabled": True,  # 连续亏损停机开关
            "consecutive_loss_stop_count": 3,  # 连续亏损达到 N 次自动停机
            "max_position_value": 50000,  # 单个持仓最大市值
            "max_total_exposure": 0.5,  # 总仓位上限 (50%)
            "min_cash_ratio": 0.3,  # 最小现金比例 (30%)
            "same_direction_max_new_orders_per_scan": 2,  # 单轮同方向（买入）最多新单数
            "max_concurrent_long_positions": 8,  # 最多同时持有的多头标的数
            "ml_filter_enabled": False,
            "ml_model_type": "logreg",  # logreg | random_forest | gbdt
            "ml_threshold": 0.60,
            "ml_horizon_days": 5,
            "ml_train_ratio": 0.70,
            "ml_walk_forward_windows": 4,
            "ml_filter_cache_minutes": 15,
            "research_allocation_enabled": False,
            "research_allocation_max_age_minutes": 0,
            "research_allocation_notional_scale": 1.0,
            # 为 True 时，扫描评分会并入策略参数矩阵结果中 matrix_score 排名前 3 的变体（含 strategy_params）
            "merge_strategy_matrix_top3": False,
            "strategies": list_strategy_names(),
            "pair_pool": dict(DEFAULT_PAIR_POOL),
            "universe": {
                "us": [
                    "AAPL.US",
                    "ABBV.US",
                    "ABNB.US",
                    "ADP.US",
                    "ADSK.US",
                    "AEP.US",
                    "AES.US",
                    "AI.US",
                    "AMAT.US",
                    "AMD.US",
                    "AMZN.US",
                    "ANET.US",
                    "APLD.US",
                    "APP.US",
                    "ARM.US",
                    "ASML.US",
                    "AVGO.US",
                    "AXP.US",
                    "BA.US",
                    "BAC.US",
                    "BBAI.US",
                    "BKNG.US",
                    "BLK.US",
                    "CAT.US",
                    "CDNS.US",
                    "CFLT.US",
                    "CMCSA.US",
                    "COF.US",
                    "COIN.US",
                    "COP.US",
                    "COST.US",
                    "CRM.US",
                    "CRWD.US",
                    "CVX.US",
                    "DDOG.US",
                    "DE.US",
                    "DELL.US",
                    "DIS.US",
                    "DUOL.US",
                    "DXYZ.US",
                    "ESTC.US",
                    "FDX.US",
                    "FTNT.US",
                    "GE.US",
                    "GLW.US",
                    "GOOGL.US",
                    "GS.US",
                    "HD.US",
                    "HON.US",
                    "HOOD.US",
                    "HPE.US",
                    "IBM.US",
                    "ICE.US",
                    "INTC.US",
                    "INTU.US",
                    "ISRG.US",
                    "JNJ.US",
                    "JPM.US",
                    "KLAC.US",
                    "LLY.US",
                    "LMT.US",
                    "LOW.US",
                    "LRCX.US",
                    "LUNR.US",
                    "MA.US",
                    "MCD.US",
                    "MCHP.US",
                    "MDB.US",
                    "META.US",
                    "MPWR.US",
                    "MRNA.US",
                    "MRVL.US",
                    "MS.US",
                    "MSFT.US",
                    "MU.US",
                    "NEE.US",
                    "NET.US",
                    "NFLX.US",
                    "NKE.US",
                    "NOW.US",
                    "NTAP.US",
                    "NVDA.US",
                    "NVO.US",
                    "NVTS.US",
                    "OKLO.US",
                    "ON.US",
                    "ORCL.US",
                    "PANW.US",
                    "PATH.US",
                    "PLTR.US",
                    "PYPL.US",
                    "QBTS.US",
                    "QRVO.US",
                    "QUBT.US",
                    "RKLX.US",
                    "RLBN.US",
                    "RTX.US",
                    "RXLB.US",
                    "RXRX.US",
                    "S.US",
                    "SBUX.US",
                    "SCHW.US",
                    "SHOP.US",
                    "SMCI.US",
                    "SNDK.US",
                    "SNOW.US",
                    "SNPS.US",
                    "SOUN.US",
                    "SPGI.US",
                    "STX.US",
                    "SWKS.US",
                    "T.US",
                    "TEAM.US",
                    "TER.US",
                    "TGT.US",
                    "TMUS.US",
                    "TSLA.US",
                    "TSM.US",
                    "U.US",
                    "UBER.US",
                    "UNH.US",
                    "UPS.US",
                    "USAR.US",
                    "USAU.US",
                    "V.US",
                    "VEEV.US",
                    "VRT.US",
                    "VZ.US",
                    "WDC.US",
                    "WMT.US",
                    "XOM.US",
                    "ZS.US",
                ],
                "hk": [
                    "00001.HK",
                    "00002.HK",
                    "00006.HK",
                    "00016.HK",
                    "00019.HK",
                    "00027.HK",
                    "00175.HK",
                    "00386.HK",
                    "00388.HK",
                    "00688.HK",
                    "00700.HK",
                    "00728.HK",
                    "00762.HK",
                    "00857.HK",
                    "00883.HK",
                    "00939.HK",
                    "00941.HK",
                    "01024.HK",
                    "01088.HK",
                    "01093.HK",
                    "01109.HK",
                    "01177.HK",
                    "01211.HK",
                    "01288.HK",
                    "01299.HK",
                    "01336.HK",
                    "01398.HK",
                    "01787.HK",
                    "01801.HK",
                    "01810.HK",
                    "01928.HK",
                    "02020.HK",
                    "02202.HK",
                    "02269.HK",
                    "02318.HK",
                    "02319.HK",
                    "02328.HK",
                    "02628.HK",
                    "02899.HK",
                    "03690.HK",
                    "03968.HK",
                    "03988.HK",
                    "06618.HK",
                    "06690.HK",
                    "09618.HK",
                    "09633.HK",
                    "09866.HK",
                    "09888.HK",
                    "09988.HK",
                    "09999.HK",
                ],
                "cn": [
                    "000333.SZ",
                    "000568.SZ",
                    "000651.SZ",
                    "000858.SZ",
                    "002475.SZ",
                    "002594.SZ",
                    "300014.SZ",
                    "300015.SZ",
                    "300033.SZ",
                    "300059.SZ",
                    "300122.SZ",
                    "300124.SZ",
                    "300223.SZ",
                    "300274.SZ",
                    "300308.SZ",
                    "300316.SZ",
                    "300347.SZ",
                    "300394.SZ",
                    "300408.SZ",
                    "300418.SZ",
                    "300433.SZ",
                    "300450.SZ",
                    "300454.SZ",
                    "300474.SZ",
                    "300476.SZ",
                    "300496.SZ",
                    "300498.SZ",
                    "300502.SZ",
                    "300628.SZ",
                    "300661.SZ",
                    "300750.SZ",
                    "300751.SZ",
                    "300760.SZ",
                    "300782.SZ",
                    "300896.SZ",
                    "300979.SZ",
                    "600030.SH",
                    "600036.SH",
                    "600276.SH",
                    "600519.SH",
                    "600809.SH",
                    "600900.SH",
                    "601012.SH",
                    "601088.SH",
                    "601166.SH",
                    "601318.SH",
                    "601899.SH",
                    "688012.SH",
                    "688256.SH",
                    "688981.SH",
                ],
            },
        }
        self._config: dict[str, Any] = self._load_or_default_config()
        self._pipeline = self._make_pipeline(self._config)
        self._last_scan_at: Optional[str] = None
        self._last_scan_summary: dict[str, Any] = {}
        # 定时调度线程（_loop）专用：与手动触发扫描区分，便于 runtime 判断是否卡死或连续失败
        self._scheduler_scan_in_progress: bool = False
        self._scheduler_scan_started_at: Optional[str] = None
        self._scheduler_scan_finished_at: Optional[str] = None
        self._scheduler_last_error: Optional[str] = None
        self._signals: dict[str, dict[str, Any]] = {}
        self._executed_trades: list[dict[str, Any]] = []  # 记录已执行的交易
        self._ml_filter_cache: dict[str, dict[str, Any]] = {}
        self._daily_trade_count: int = 0
        self._last_trade_date: str = ""
        self._daily_start_equity: Optional[float] = None
        self._daily_last_equity: Optional[float] = None
        self._daily_loss_pct: float = 0.0
        self._daily_loss_circuit_triggered: bool = False
        self._daily_loss_circuit_reason: str = ""
        self._daily_loss_circuit_at: Optional[str] = None
        self._consecutive_loss_count: int = 0
        self._consecutive_loss_stop_triggered: bool = False
        self._consecutive_loss_stop_reason: str = ""
        self._consecutive_loss_stop_at: Optional[str] = None
        self._last_trade_pnl_estimate: Optional[float] = None
        self._consecutive_no_signal_rounds: int = 0
        self._last_observer_push_at: Optional[str] = None
        self._research_scan_ctx: Optional[dict[str, Any]] = None
        self._last_research_allocation_ctx: Optional[dict[str, Any]] = None
        # 单轮扫描内复用 K 线，减少重启后的首轮重复拉取耗时。
        self._scan_bars_cache: dict[tuple[str, int, str], list[Bar]] = {}
        self._scan_round_reset_key_by_market: dict[str, str] = {"us": "", "hk": "", "cn": ""}
        self._scan_round_in_day_by_market: dict[str, int] = {"us": 0, "hk": 0, "cn": 0}
        self._restore_signals_on_boot()
        self._restore_scan_counter_on_boot()

        self._strategy_templates: dict[str, dict[str, Any]] = {
            "trend": {
                "label": "趋势",
                "description": "偏趋势跟随，仓位相对积极。",
                "entry_rule": "breakout",
                "breakout_lookback_bars": 20,
                "breakout_volume_ratio": 1.2,
                "exit_rules": ["hard_stop", "take_profit", "strategy_sell"],
                "rule_priority": ["hard_stop", "take_profit", "strategy_sell", "time_stop"],
                "hard_stop_pct": 7.0,
                "take_profit_pct": 18.0,
                "time_stop_hours": 96,
                "sizer": {"type": "risk_percent", "quantity": 100, "risk_pct": 0.015, "target_vol_pct": 0.02},
                "cost_model": {"commission_bps": 3, "slippage_bps": 6},
            },
            "mean_reversion": {
                "label": "均值回归",
                "description": "偏短线回归，止盈止损更紧。",
                "entry_rule": "mean_reversion",
                "mean_reversion_rsi_threshold": 35.0,
                "mean_reversion_deviation_pct": 2.0,
                "exit_rules": ["hard_stop", "take_profit", "time_stop", "strategy_sell"],
                "rule_priority": ["hard_stop", "take_profit", "time_stop", "strategy_sell"],
                "hard_stop_pct": 4.0,
                "take_profit_pct": 8.0,
                "time_stop_hours": 36,
                "sizer": {"type": "fixed", "quantity": 100, "risk_pct": 0.01, "target_vol_pct": 0.02},
                "cost_model": {"commission_bps": 3, "slippage_bps": 4},
            },
            "defensive": {
                "label": "防守",
                "description": "偏保守，控制回撤优先。",
                "entry_rule": "strategy_cross",
                "exit_rules": ["hard_stop", "time_stop", "strategy_sell", "take_profit"],
                "rule_priority": ["hard_stop", "time_stop", "strategy_sell", "take_profit"],
                "hard_stop_pct": 3.0,
                "take_profit_pct": 10.0,
                "time_stop_hours": 48,
                "sizer": {"type": "volatility", "quantity": 100, "risk_pct": 0.008, "target_vol_pct": 0.012},
                "cost_model": {"commission_bps": 3, "slippage_bps": 5},
            },
        }

    def _normalize_symbols(self, symbols: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for s in symbols:
            x = str(s).strip().upper()
            if not x:
                continue
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    def _load_or_default_config(self) -> dict[str, Any]:
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                merged = dict(self._default_config)
                merged.update(loaded)
                return merged
            except Exception:
                pass
        return dict(self._default_config)

    def _save_config(self) -> None:
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_backups(self) -> list[dict[str, Any]]:
        if not os.path.exists(self._backup_path):
            return []
        try:
            with open(self._backup_path, "r", encoding="utf-8") as f:
                rows = json.load(f)
            if isinstance(rows, list):
                return rows
        except Exception:
            pass
        return []

    def _save_backups(self, rows: list[dict[str, Any]]) -> None:
        try:
            with open(self._backup_path, "w", encoding="utf-8") as f:
                json.dump(rows[: self._max_backups], f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _backup_current_config_locked(self, reason: str = "update") -> None:
        rows = self._load_backups()
        snap = {
            "id": f"BKP-{uuid4().hex[:12].upper()}",
            "created_at": datetime.now().isoformat(),
            "reason": reason,
            "config": deepcopy(self._config),
        }
        rows.insert(0, snap)
        self._save_backups(rows[: self._max_backups])

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._config)

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if updates:
                self._backup_current_config_locked(reason="update_config")
            self._config.update(updates)
            if updates.get("enabled", None) is True:
                self._consecutive_loss_stop_triggered = False
                self._consecutive_loss_stop_reason = ""
                self._consecutive_loss_stop_at = None
                self._consecutive_loss_count = 0
            self._pipeline = self._make_pipeline(self._config)
            self._save_config()
            return dict(self._config)

    def list_config_backups(self) -> list[dict[str, Any]]:
        rows = self._load_backups()
        out: list[dict[str, Any]] = []
        for x in rows:
            cfg = x.get("config", {}) or {}
            out.append(
                {
                    "id": x.get("id"),
                    "created_at": x.get("created_at"),
                    "reason": x.get("reason"),
                    "active_template": cfg.get("active_template", "custom"),
                    "market": cfg.get("market", "us"),
                    "kline": cfg.get("kline", "1d"),
                }
            )
        return out

    def rollback_config(self, backup_id: str) -> dict[str, Any]:
        rows = self._load_backups()
        target = next((x for x in rows if str(x.get("id")) == str(backup_id)), None)
        if not target:
            raise ValueError(f"backup not found: {backup_id}")
        cfg = target.get("config")
        if not isinstance(cfg, dict):
            raise ValueError("backup payload invalid")
        with self._lock:
            self._backup_current_config_locked(reason=f"rollback_from:{backup_id}")
            merged = dict(self._default_config)
            merged.update(deepcopy(cfg))
            self._config = merged
            self._pipeline = self._make_pipeline(self._config)
            self._save_config()
            return dict(self._config)

    def preview_rollback(self, backup_id: str) -> dict[str, Any]:
        rows = self._load_backups()
        target = next((x for x in rows if str(x.get("id")) == str(backup_id)), None)
        if not target:
            raise ValueError(f"backup not found: {backup_id}")
        bcfg = target.get("config")
        if not isinstance(bcfg, dict):
            raise ValueError("backup payload invalid")
        current = self.get_config()
        keys = sorted(set(current.keys()) | set(bcfg.keys()))
        diff: dict[str, dict[str, Any]] = {}
        for k in keys:
            old = current.get(k)
            new = bcfg.get(k)
            if old != new:
                diff[k] = {"from": old, "to": new}
        return {
            "backup_id": backup_id,
            "backup_created_at": target.get("created_at"),
            "backup_reason": target.get("reason"),
            "diff": diff,
        }

    def list_templates(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key, payload in self._strategy_templates.items():
            out.append(
                {
                    "name": key,
                    "label": payload.get("label", key),
                    "description": payload.get("description", ""),
                    "config": {
                        "entry_rule": payload.get("entry_rule"),
                        "breakout_lookback_bars": payload.get("breakout_lookback_bars"),
                        "breakout_volume_ratio": payload.get("breakout_volume_ratio"),
                        "mean_reversion_rsi_threshold": payload.get("mean_reversion_rsi_threshold"),
                        "mean_reversion_deviation_pct": payload.get("mean_reversion_deviation_pct"),
                        "exit_rules": list(payload.get("exit_rules", [])),
                        "rule_priority": list(payload.get("rule_priority", [])),
                        "sizer": dict(payload.get("sizer", {})),
                        "cost_model": dict(payload.get("cost_model", {})),
                        "hard_stop_pct": payload.get("hard_stop_pct"),
                        "take_profit_pct": payload.get("take_profit_pct"),
                        "time_stop_hours": payload.get("time_stop_hours"),
                    },
                }
            )
        return out

    def preview_template(self, name: str) -> dict[str, Any]:
        key = str(name).strip().lower()
        t = self._strategy_templates.get(key)
        if not t:
            raise ValueError(f"unknown template: {name}")
        current = self.get_config()
        patch = {
            "active_template": key,
            "entry_rule": t.get("entry_rule", "strategy_cross"),
            "breakout_lookback_bars": int(t.get("breakout_lookback_bars", current.get("breakout_lookback_bars", 20))),
            "breakout_volume_ratio": float(t.get("breakout_volume_ratio", current.get("breakout_volume_ratio", 1.2))),
            "mean_reversion_rsi_threshold": float(
                t.get("mean_reversion_rsi_threshold", current.get("mean_reversion_rsi_threshold", 35.0))
            ),
            "mean_reversion_deviation_pct": float(
                t.get("mean_reversion_deviation_pct", current.get("mean_reversion_deviation_pct", 2.0))
            ),
            "exit_rules": list(t.get("exit_rules", [])),
            "rule_priority": list(t.get("rule_priority", [])),
            "sizer": dict(t.get("sizer", {})),
            "cost_model": dict(t.get("cost_model", {})),
            "hard_stop_pct": float(t.get("hard_stop_pct", 6.0)),
            "take_profit_pct": float(t.get("take_profit_pct", 12.0)),
            "time_stop_hours": int(t.get("time_stop_hours", 72)),
        }
        proposed = dict(current)
        proposed.update(patch)
        diff: dict[str, dict[str, Any]] = {}
        for k, v in patch.items():
            old = current.get(k)
            if old != v:
                diff[k] = {"from": old, "to": v}
        return {
            "name": key,
            "label": t.get("label", key),
            "description": t.get("description", ""),
            "current": current,
            "proposed": proposed,
            "diff": diff,
        }

    def apply_template(self, name: str) -> dict[str, Any]:
        key = str(name).strip().lower()
        t = self._strategy_templates.get(key)
        if not t:
            raise ValueError(f"unknown template: {name}")
        updates = {
            "active_template": key,
            "entry_rule": t.get("entry_rule", "strategy_cross"),
            "breakout_lookback_bars": int(
                t.get("breakout_lookback_bars", self._config.get("breakout_lookback_bars", 20))
            ),
            "breakout_volume_ratio": float(
                t.get("breakout_volume_ratio", self._config.get("breakout_volume_ratio", 1.2))
            ),
            "mean_reversion_rsi_threshold": float(
                t.get("mean_reversion_rsi_threshold", self._config.get("mean_reversion_rsi_threshold", 35.0))
            ),
            "mean_reversion_deviation_pct": float(
                t.get("mean_reversion_deviation_pct", self._config.get("mean_reversion_deviation_pct", 2.0))
            ),
            "exit_rules": list(t.get("exit_rules", [])),
            "rule_priority": list(t.get("rule_priority", [])),
            "sizer": dict(t.get("sizer", {})),
            "cost_model": dict(t.get("cost_model", {})),
            "hard_stop_pct": float(t.get("hard_stop_pct", 6.0)),
            "take_profit_pct": float(t.get("take_profit_pct", 12.0)),
            "time_stop_hours": int(t.get("time_stop_hours", 72)),
        }
        return self.update_config(updates)

    def _get_universe(self, market: str) -> list[str]:
        with self._lock:
            uni = self._config.get("universe", {})
        symbols = uni.get(market, uni.get("us", []))
        return self._normalize_symbols(symbols)

    def _make_pipeline(self, cfg: dict[str, Any]) -> StrategyPipeline:
        return StrategyPipeline(
            fetch_bars=self._fetch_bars,
            entry_rule=self._build_entry_rule(cfg),
            exit_rules=self._build_exit_rules(cfg),
            sizer=self._build_sizer(cfg),
            guards=[SymbolCooldownGuard(), DailyTradeLimitGuard(), ExistingPositionGuard()],
        )

    def _build_entry_rule(self, cfg: dict[str, Any]) -> Any:
        rule = str(cfg.get("entry_rule", "strategy_cross")).strip().lower()
        if rule == "breakout":
            return BreakoutRule(
                lookback_bars=int(cfg.get("breakout_lookback_bars", 20) or 20),
                min_volume_ratio=float(cfg.get("breakout_volume_ratio", 1.2) or 0.0),
            )
        if rule == "mean_reversion":
            return MeanReversionRule(
                rsi_threshold=float(cfg.get("mean_reversion_rsi_threshold", 35.0) or 35.0),
                ma_period=20,
                deviation_pct=float(cfg.get("mean_reversion_deviation_pct", 2.0) or 0.0),
            )
        return StrategyCrossRule()

    def _build_exit_rules(self, cfg: dict[str, Any]) -> list[Any]:
        mapping = {
            "hard_stop": HardStopRule(stop_loss_pct=float(cfg.get("hard_stop_pct", 6.0) or 6.0)),
            "take_profit": TakeProfitRule(take_profit_pct=float(cfg.get("take_profit_pct", 12.0) or 12.0)),
            "strategy_sell": StrategySellRule(),
            "time_stop": TimeStopRule(max_hold_hours=int(cfg.get("time_stop_hours", 72) or 72)),
        }
        enabled = cfg.get("exit_rules", ["hard_stop", "take_profit", "strategy_sell"])
        priority = cfg.get("rule_priority", ["hard_stop", "take_profit", "strategy_sell", "time_stop"])
        ordered: list[Any] = []
        for name in priority:
            if name in enabled and name in mapping:
                ordered.append(mapping[name])
        for name in enabled:
            if name in mapping and mapping[name] not in ordered:
                ordered.append(mapping[name])
        return ordered

    def _build_sizer(self, cfg: dict[str, Any]) -> Any:
        sizer_cfg = cfg.get("sizer", {}) or {}
        stype = str(sizer_cfg.get("type", "fixed")).strip().lower()
        if stype == "risk_percent":
            return RiskPercentSizer(risk_pct=float(sizer_cfg.get("risk_pct", 0.01) or 0.01))
        if stype == "volatility":
            return VolatilitySizer(target_vol_pct=float(sizer_cfg.get("target_vol_pct", 0.02) or 0.02))
        qty = int(sizer_cfg.get("quantity", cfg.get("order_quantity", 100)) or 100)
        return FixedSizer(quantity=max(1, qty))

    def _estimate_cost_pct(self, trades: int, cfg: dict[str, Any]) -> float:
        cost = cfg.get("cost_model", {}) or {}
        commission_bps = float(cost.get("commission_bps", 3) or 0)
        slippage_bps = float(cost.get("slippage_bps", 5) or 0)
        per_trade_pct = (commission_bps + slippage_bps) / 100.0
        return max(0.0, trades * per_trade_pct)

    def _ml_cache_key(self, symbol: str, cfg: dict[str, Any]) -> str:
        payload = {
            "symbol": symbol,
            "kline": str(cfg.get("kline", "1d")),
            "bars_days": int(cfg.get("signal_bars_days", 90) or 90),
            "model_type": str(cfg.get("ml_model_type", "logreg")),
            "threshold": float(cfg.get("ml_threshold", 0.6) or 0.6),
            "horizon_days": int(cfg.get("ml_horizon_days", 5) or 5),
            "train_ratio": float(cfg.get("ml_train_ratio", 0.7) or 0.7),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _ml_probability_for_symbol(
        self, symbol: str, cfg: dict[str, Any]
    ) -> tuple[Optional[float], Optional[dict[str, Any]]]:
        """训练轻量模型并返回当前上涨概率。"""
        ttl_min = max(0, min(int(cfg.get("ml_filter_cache_minutes", 15) or 15), 24 * 60))
        ck = self._ml_cache_key(symbol, cfg)
        now = datetime.now()
        if ttl_min > 0:
            cached = self._ml_filter_cache.get(ck)
            if cached and isinstance(cached.get("expires_at"), datetime) and now < cached["expires_at"]:
                return cached.get("up_probability"), cached.get("walk_forward")

        days = max(90, min(int(cfg.get("signal_bars_days", 90) or 90), 365))
        kline = str(cfg.get("kline", "1d"))
        bars = self._fetch_bars(symbol, days, kline)
        if len(bars) < 100:
            return None, None

        try:
            cost_cfg = cfg.get("cost_model", {}) or {}
            one_way_bps = float(cost_cfg.get("commission_bps", 3) or 0) + float(
                cost_cfg.get("slippage_bps", 5) or 0
            )
            df = build_ml_feature_frame(
                bars,
                horizon_days=int(cfg.get("ml_horizon_days", 5) or 5),
                transaction_cost_bps=max(0.0, one_way_bps * 2.0),
            )
        except Exception:
            return None, None
        if df is None:
            return None, None
        if len(df) < 80:
            return None, None

        feature_cols = FEATURE_COLUMNS
        X = df[feature_cols].astype(float).values
        y = df["label"].astype(int).values
        tr = max(0.5, min(float(cfg.get("ml_train_ratio", 0.7) or 0.7), 0.9))
        mt = str(cfg.get("ml_model_type", "logreg")).lower()
        wf_windows = max(1, min(int(cfg.get("ml_walk_forward_windows", 4) or 4), 10))
        try:
            _, wf_summary = walk_forward_probability_map(
                df=df,
                model_type=mt,
                train_ratio=tr,
                min_train_size=60,
                test_window=20,
                max_windows=wf_windows,
            )
        except Exception:
            wf_summary = None
        if len(set(y.tolist())) < 2:
            return None, wf_summary
        try:
            model = create_ml_classifier(mt)
        except Exception:
            return None, wf_summary
        model.fit(X, y)
        prob = float(model.predict_proba(X[-1].reshape(1, -1))[0, 1])

        if ttl_min > 0:
            self._ml_filter_cache[ck] = {
                "up_probability": prob,
                "walk_forward": wf_summary,
                "expires_at": now + timedelta(minutes=ttl_min),
            }
        return prob, wf_summary

    def _ml_passes_for_buy(self, symbol: str, cfg: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """ML过滤器：仅拦截买入。"""
        if not bool(cfg.get("ml_filter_enabled", False)):
            return True, {"enabled": False}
        threshold = max(0.5, min(float(cfg.get("ml_threshold", 0.6) or 0.6), 0.95))
        p, wf_summary = self._ml_probability_for_symbol(symbol, cfg)
        if p is None:
            return False, {
                "enabled": True,
                "passed": False,
                "reason": "ml_probability_unavailable",
                "threshold": threshold,
                "model_type": str(cfg.get("ml_model_type", "logreg")),
                "walk_forward": wf_summary,
            }
        passed = p >= threshold
        return passed, {
            "enabled": True,
            "passed": passed,
            "up_probability": round(p, 4),
            "threshold": threshold,
            "model_type": str(cfg.get("ml_model_type", "logreg")),
            "horizon_days": int(cfg.get("ml_horizon_days", 5) or 5),
            "walk_forward": wf_summary,
        }

    def _get_pair_symbols(self, market: str) -> list[str]:
        """获取配对模式下的所有股票代码"""
        with self._lock:
            cfg = dict(self._config)
        pool = normalize_pair_pool(cfg.get("pair_pool"))
        return flatten_pair_symbols(pool, market)

    def _prune_invalid_symbol(self, symbol: str, market: str) -> bool:
        """从 universe / pair_pool 中移除无效代码并持久化。"""
        sym = str(symbol).strip().upper()
        if not sym:
            return False
        changed = False
        with self._lock:
            universe = dict(self._config.get("universe", {}))
            market_universe = list(universe.get(market, []))
            new_universe = [s for s in market_universe if str(s).strip().upper() != sym]
            if len(new_universe) != len(market_universe):
                universe[market] = new_universe
                self._config["universe"] = universe
                changed = True

            pair_pool = dict(self._config.get("pair_pool", {}))
            market_pairs = dict(pair_pool.get(market, {}))
            new_pairs: dict[str, str] = {}
            for k, v in market_pairs.items():
                ku = str(k).strip().upper()
                vu = str(v).strip().upper()
                if ku == sym or vu == sym:
                    changed = True
                    continue
                new_pairs[k] = v
            if len(new_pairs) != len(market_pairs):
                pair_pool[market] = new_pairs
                self._config["pair_pool"] = pair_pool
                changed = True

            if changed:
                self._save_config()
        return changed

    def screen_strong_stocks(
        self,
        market: str = "us",
        limit: int = 8,
        kline: str = "1d",
    ) -> list[dict[str, Any]]:
        symbols = self._get_universe(market)
        out: list[dict[str, Any]] = []
        # 20日强弱计算至少需要21根K线；A/H 市场在30个自然日内常不足21个交易日，
        # 因此这里拉取更长窗口，避免被误判为“无数据”。
        bars_days = 60
        min_required_bars = 21
        for sym in symbols:
            try:
                bars = self._fetch_bars_for_scan(sym, bars_days, kline)
                if len(bars) < min_required_bars:
                    continue
                c0 = float(bars[-1].close)
                c5 = float(bars[-6].close)
                c20 = float(bars[-21].close)
                ret5 = (c0 - c5) / c5 * 100 if c5 else 0.0
                ret20 = (c0 - c20) / c20 * 100 if c20 else 0.0
                score = ret20 * 0.6 + ret5 * 0.4
                q = self._quote_last(sym) or {}
                out.append(
                    {
                        "symbol": sym,
                        "last": q.get("last", c0),
                        "change_pct": q.get("change_pct", 0.0),
                        "price_type": q.get("price_type", "K线收盘"),
                        "price_source": "realtime_quote" if q.get("last") is not None else "kline_close",
                        "ret5_pct": round(ret5, 2),
                        "ret20_pct": round(ret20, 2),
                        "strength_score": round(score, 2),
                    }
                )
            except Exception:
                continue
        out.sort(key=lambda x: x["strength_score"], reverse=True)
        return out[: max(1, int(limit))]

    @staticmethod
    def _merge_matrix_top3_specs(
        base_specs: list[tuple[str, Optional[dict[str, Any]], str]],
        cfg: dict[str, Any],
    ) -> list[tuple[str, Optional[dict[str, Any]], str]]:
        if not bool(cfg.get("merge_strategy_matrix_top3", False)):
            return base_specs
        try:
            from api.auto_trader_research import get_strategy_param_matrix_result, get_research_snapshot_history_result
        except Exception:
            return base_specs
        mkt = str(cfg.get("market", "us") or "us")
        snap_id = str(cfg.get("merge_strategy_matrix_top3_snapshot_id") or "").strip()
        if snap_id:
            mat = get_research_snapshot_history_result(
                market=mkt,
                history_type="strategy_matrix",
                snapshot_id=snap_id,
            )
            if not isinstance(mat, dict) or not mat:
                mat = get_strategy_param_matrix_result(mkt)
        else:
            mat = get_strategy_param_matrix_result(mkt)
        items = mat.get("items") if isinstance(mat, dict) else None
        if not isinstance(items, list) or not items:
            return base_specs
        mat_m = str(mat.get("market", "") or "").strip().lower()
        if mat_m and mat_m != str(mkt).strip().lower():
            return base_specs
        ranked = sorted(
            (x for x in items if isinstance(x, dict) and str(x.get("strategy", "")).strip()),
            key=lambda x: float(x.get("matrix_score", -9999) or -9999),
            reverse=True,
        )
        seen = {_strategy_spec_key(a, b) for a, b, _ in base_specs}
        out = list(base_specs)
        for i, row in enumerate(ranked[:3]):
            sn = str(row.get("strategy", "")).strip()
            sp = row.get("strategy_params")
            sp = dict(sp) if isinstance(sp, dict) else {}
            params: Optional[dict[str, Any]] = sp if sp else None
            k = _strategy_spec_key(sn, params)
            if k in seen:
                continue
            seen.add(k)
            out.append((sn, params, f"matrix_top_{i + 1}"))
        return out

    def _score_strategy_specs(
        self,
        symbol: str,
        specs: list[tuple[str, Optional[dict[str, Any]], str]],
        days: int,
        kline: str,
        initial_capital: float,
    ) -> list[dict[str, Any]]:
        with self._lock:
            cfg = dict(self._config)
        try:
            bars = self._fetch_bars_for_scan(symbol, days, kline)
        except Exception as e:
            return [{"strategy": "__fetch_bars__", "error": f"{symbol} fetch failed: {e}", "composite_score": -9999.0}]
        if not bars:
            return []
        rows: list[dict[str, Any]] = []
        for sname, params, label in specs:
            try:
                sfn = get_strategy(sname, params)
                engine = BacktestEngine(
                    bars=bars,
                    symbol=symbol,
                    strategy_name=sfn.__name__,
                    strategy_fn=sfn,
                    initial_capital=initial_capital,
                )
                r = engine.run()
                est_cost_pct = self._estimate_cost_pct(int(r.total_trades), cfg)
                net_return_pct = float(r.total_return_pct) - est_cost_pct
                composite = net_return_pct - 0.5 * float(r.max_drawdown_pct) + 5.0 * float(r.sharpe_ratio)
                rows.append(
                    {
                        "strategy": sname,
                        "strategy_label": r.strategy_name,
                        "strategy_params": dict(params) if isinstance(params, dict) else {},
                        "scoring_source": label,
                        "total_return_pct": round(float(r.total_return_pct), 2),
                        "est_cost_pct": round(est_cost_pct, 2),
                        "net_return_pct": round(net_return_pct, 2),
                        "max_drawdown_pct": round(float(r.max_drawdown_pct), 2),
                        "sharpe_ratio": round(float(r.sharpe_ratio), 2),
                        "win_rate_pct": round(float(r.win_rate_pct), 2),
                        "trades": int(r.total_trades),
                        "composite_score": round(composite, 2),
                    }
                )
            except Exception as e:
                rows.append({"strategy": sname, "error": str(e), "composite_score": -9999.0, "scoring_source": label})
        rows.sort(key=lambda x: x.get("composite_score", -9999.0), reverse=True)
        return rows

    def score_strategies(
        self,
        symbol: str,
        strategies: list[str],
        days: int = 120,
        kline: str = "1d",
        initial_capital: float = 100000.0,
        strategy_params_map: Optional[dict[str, dict[str, Any]]] = None,
        cfg: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        specs: list[tuple[str, Optional[dict[str, Any]], str]] = []
        seen: set[str] = set()
        pmap = strategy_params_map if isinstance(strategy_params_map, dict) else {}
        for sname in strategies or []:
            sn = str(sname).strip()
            if not sn:
                continue
            p = pmap.get(sn)
            params = dict(p) if isinstance(p, dict) else None
            k = _strategy_spec_key(sn, params)
            if k in seen:
                continue
            seen.add(k)
            specs.append((sn, params, sn))
        merge_cfg = cfg if isinstance(cfg, dict) else None
        if merge_cfg is not None:
            specs = self._merge_matrix_top3_specs(specs, merge_cfg)
        return self._score_strategy_specs(symbol, specs, days, kline, initial_capital)

    def _reset_daily_counter_if_needed(self) -> None:
        """重置每日交易计数器"""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            if self._last_trade_date != today:
                self._daily_trade_count = 0
                self._last_trade_date = today
                self._daily_start_equity = None
                self._daily_last_equity = None
                self._daily_loss_pct = 0.0
                self._daily_loss_circuit_triggered = False
                self._daily_loss_circuit_reason = ""
                self._daily_loss_circuit_at = None

    @staticmethod
    def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    def _get_position_cost_price(self, symbol: str) -> Optional[float]:
        try:
            positions = self._get_positions() or {}
            rows = positions.get("positions", [])
            if not isinstance(rows, list):
                return None
            target = str(symbol or "").upper()
            for p in rows:
                if not isinstance(p, dict):
                    continue
                if str(p.get("symbol", "")).upper() != target:
                    continue
                # 不同券商字段命名不一致，按常见字段依次回退。
                for key in ("cost_price", "avg_cost", "average_cost", "open_price", "cost_basis_price"):
                    x = self._safe_float(p.get(key), None)
                    if x is not None and x > 0:
                        return x
                qty = self._safe_float(p.get("quantity"), 0.0) or 0.0
                cost_basis = self._safe_float(p.get("cost_basis"), None)
                if cost_basis is not None and qty > 0:
                    return float(cost_basis) / float(qty)
        except Exception:
            return None
        return None

    def _update_consecutive_loss_state_after_trade(
        self, action: str, symbol: str, quantity: int, price: float
    ) -> None:
        if str(action).lower() != "sell":
            return
        cost = self._get_position_cost_price(symbol)
        if cost is None or cost <= 0:
            return
        pnl = (float(price) - float(cost)) * max(0, int(quantity))
        with self._lock:
            self._last_trade_pnl_estimate = round(float(pnl), 4)
            if pnl < 0:
                self._consecutive_loss_count += 1
            else:
                self._consecutive_loss_count = 0

            cfg = dict(self._config)
            enabled = bool(cfg.get("consecutive_loss_stop_enabled", True))
            threshold = max(1, int(cfg.get("consecutive_loss_stop_count", 3) or 3))
            if enabled and self._consecutive_loss_count >= threshold and not self._consecutive_loss_stop_triggered:
                self._consecutive_loss_stop_triggered = True
                self._consecutive_loss_stop_at = datetime.now().isoformat()
                self._consecutive_loss_stop_reason = (
                    f"consecutive_loss_count={self._consecutive_loss_count} >= threshold={threshold}"
                )
                # 触发后停自动扫描，保留配置可见性，便于人工恢复。
                self._config["enabled"] = False
                self._save_config()

    def _update_daily_loss_state(self, total_assets: float, limit_pct: float) -> None:
        if total_assets <= 0:
            return
        with self._lock:
            if self._daily_start_equity is None or self._daily_start_equity <= 0:
                self._daily_start_equity = float(total_assets)
            self._daily_last_equity = float(total_assets)
            start = float(self._daily_start_equity or 0.0)
            if start <= 0:
                self._daily_loss_pct = 0.0
                return
            loss_pct = max(0.0, (start - float(total_assets)) / start)
            self._daily_loss_pct = float(loss_pct)
            if loss_pct >= max(0.0, float(limit_pct)) and not self._daily_loss_circuit_triggered:
                self._daily_loss_circuit_triggered = True
                self._daily_loss_circuit_at = datetime.now().isoformat()
                self._daily_loss_circuit_reason = (
                    f"daily_loss_pct={loss_pct:.4f} >= limit_pct={float(limit_pct):.4f}"
                )

    def _check_risk_limits(self, symbol: str, quantity: int, price: float, action: str = "buy") -> tuple[bool, str]:
        """检查风控限制
        
        返回: (是否通过, 拒绝原因)
        """
        self._reset_daily_counter_if_needed()
        
        with self._lock:
            cfg = dict(self._config)
            daily_count = self._daily_trade_count
        
        # 检查每日交易次数
        max_daily = cfg.get("max_daily_trades", 5)
        if daily_count >= max_daily:
            return False, f"已达到每日最大交易次数限制 ({max_daily})"
        
        # 获取账户信息
        try:
            account = self._get_account() or {}
            # 兼容不同账户返回字段：优先使用 trade/account 的 net_assets & buy_power。
            cash = float(
                account.get("cash")
                or account.get("buy_power")
                or account.get("available_cash")
                or 0
            )
            total_assets = float(
                account.get("total_assets")
                or account.get("net_assets")
                or cash
            )
        except Exception:
            return False, "无法获取账户信息"

        daily_loss_enabled = bool(cfg.get("daily_loss_circuit_enabled", True))
        daily_loss_limit_pct = float(cfg.get("daily_loss_limit_pct", 0.03) or 0.03)
        self._update_daily_loss_state(total_assets=total_assets, limit_pct=daily_loss_limit_pct)
        
        # 卖出只检查是否有足够持仓，不做买入仓位/现金约束
        if action == "sell":
            try:
                positions = self._get_positions() or {}
                held = 0.0
                for p in positions.get("positions", []):
                    if str(p.get("symbol", "")).upper() == str(symbol).upper():
                        held = float(p.get("quantity", 0) or 0)
                        break
                if held <= 0:
                    return False, "无可卖出持仓"
                if quantity > int(held):
                    return False, f"卖出数量超过持仓（持仓 {int(held)}）"
            except Exception:
                return False, "无法校验卖出持仓"
            return True, ""

        if daily_loss_enabled:
            with self._lock:
                if self._daily_loss_circuit_triggered:
                    current_loss = float(self._daily_loss_pct or 0.0)
                    return (
                        False,
                        f"触发日损失熔断（当前{current_loss*100:.2f}% >= 阈值{daily_loss_limit_pct*100:.2f}%），仅允许减仓/卖出",
                    )

        # 检查最小现金比例（买入）
        min_cash_ratio = cfg.get("min_cash_ratio", 0.3)
        order_value = trade_value(symbol, quantity, price)
        if cash - order_value < total_assets * min_cash_ratio:
            return False, f"下单后现金将低于最小现金比例 ({min_cash_ratio*100}%)"
        
        # 检查总仓位上限
        max_exposure = cfg.get("max_total_exposure", 0.5)
        try:
            positions = self._get_positions()
            position_value = sum(
                trade_value(
                    str(p.get("symbol", "")),
                    float(p.get("quantity", 0) or 0),
                    float(p.get("current_price", 0) or 0),
                )
                for p in positions.get("positions", [])
            )
            current_exposure = position_value / total_assets if total_assets > 0 else 0
            new_exposure = (position_value + order_value) / total_assets if total_assets > 0 else 0
            if new_exposure > max_exposure:
                return False, f"下单后仓位将超过上限 ({max_exposure*100}%)"
        except Exception:
            pass
        
        # 检查单个持仓上限
        max_position_value = cfg.get("max_position_value", 50000)
        try:
            positions = self._get_positions()
            for p in positions.get("positions", []):
                if p.get("symbol") == symbol:
                    current_value = trade_value(
                        symbol,
                        float(p.get("quantity", 0) or 0),
                        float(p.get("current_price", 0) or 0),
                    )
                    new_value = current_value + order_value
                    if new_value > max_position_value:
                        return False, f"该股票持仓市值将超过上限 (${max_position_value})"
        except Exception:
            pass
        
        return True, ""

    def _execute_trade_with_risk_control(
        self, 
        action: str,
        symbol: str, 
        quantity: int, 
        strategy_info: dict[str, Any]
    ) -> dict[str, Any]:
        """执行交易（带风控）"""
        # 获取当前价格
        q = self._quote_last(symbol) or {}
        price = q.get("last", 0)
        
        if price <= 0:
            return {"success": False, "error": "无法获取有效价格"}
        
        # 风控检查
        passed, reason = self._check_risk_limits(symbol, quantity, price, action=action)
        if not passed:
            return {"success": False, "error": f"风控拦截: {reason}"}
        
        # 执行交易
        try:
            result = self._execute_trade(action, symbol, quantity, price)
            ok = True
            if isinstance(result, dict):
                # 统一判定：存在 success 且为 False 直接失败；否则当存在 order_id 视为提交成功
                if "success" in result:
                    ok = bool(result.get("success"))
                elif result.get("order_id"):
                    ok = True
            if not ok:
                err = ""
                if isinstance(result, dict):
                    err = str(result.get("error") or result.get("detail") or "")
                err = err or "交易执行失败: submit_order_not_accepted"
                return {"success": False, "error": err, "result": result}
            
            # 更新交易计数
            with self._lock:
                self._daily_trade_count += 1
                self._last_trade_date = datetime.now().strftime("%Y-%m-%d")

            self._update_consecutive_loss_state_after_trade(action, symbol, quantity, float(price))
            
            # 记录交易
            trade_record = {
                "trade_id": f"AT-{uuid4().hex[:10].upper()}",
                "symbol": symbol,
                "action": action,
                "quantity": quantity,
                "price": price,
                "value": quantity * price,
                "strategy": strategy_info.get("strategy"),
                "strategy_score": strategy_info.get("composite_score"),
                "executed_at": datetime.now().isoformat(),
                "result": result,
            }
            if isinstance(result, dict) and result.get("order_id"):
                trade_record["order_id"] = str(result.get("order_id"))
            
            with self._lock:
                self._executed_trades.append(trade_record)
                # 只保留最近100条记录
                if len(self._executed_trades) > 100:
                    self._executed_trades = self._executed_trades[-100:]
            
            return {
                "success": True,
                "trade_record": trade_record,
            }
        except Exception as e:
            return {"success": False, "error": f"交易执行失败: {e}"}

    def _restore_signals_on_boot(self) -> None:
        """
        Worker/API 进程重启后恢复最近的活动信号，避免同标的重复生成 pending。
        仅恢复：
        - pending / executing（未过期）
        - executed（最近 24h）
        """
        now = datetime.now()
        restored: dict[str, dict[str, Any]] = {}
        rows = load_persisted_signals(status="all")
        for s in rows:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("signal_id") or "").strip()
            st = str(s.get("status") or "").strip().lower()
            if not sid or not st:
                continue
            keep = False
            if st in {"pending", "executing"}:
                exp = _safe_parse_iso_datetime(s.get("expires_at"))
                keep = exp is None or now <= exp
            elif st == "executed":
                dt = _safe_parse_iso_datetime(s.get("executed_at")) or _safe_parse_iso_datetime(s.get("updated_at")) or _safe_parse_iso_datetime(s.get("created_at"))
                keep = bool(dt and (now - dt) <= timedelta(hours=24))
            if keep:
                restored[sid] = dict(s)
        if restored:
            self._signals.update(restored)

    def _restore_scan_counter_on_boot(self) -> None:
        """恢复“按市场扫描轮次计数”，worker 重启后仍沿用各市场已累计值。"""
        try:
            if not os.path.exists(AUTO_TRADER_SCAN_COUNTER_FILE):
                return
            raw = open(AUTO_TRADER_SCAN_COUNTER_FILE, "r", encoding="utf-8").read()
            if not raw.strip():
                return
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("bad_scan_counter_payload")

            # 新格式：按市场存储 reset_key / count
            keys_raw = data.get("reset_key_by_market")
            counts_raw = data.get("count_by_market")
            if isinstance(keys_raw, dict) and isinstance(counts_raw, dict):
                for m in ("us", "hk", "cn"):
                    self._scan_round_reset_key_by_market[m] = str(keys_raw.get(m) or "")
                    try:
                        self._scan_round_in_day_by_market[m] = max(0, int(counts_raw.get(m) or 0))
                    except Exception:
                        self._scan_round_in_day_by_market[m] = 0
                return

            # 兼容旧格式：date + count（迁移到当前市场）
            date_s = str(data.get("date") or "")
            cnt = max(0, int(data.get("count") or 0))
            cur_market = self._normalize_market(str(self._config.get("market", "us") or "us"))
            self._scan_round_reset_key_by_market[cur_market] = date_s
            self._scan_round_in_day_by_market[cur_market] = cnt
        except Exception:
            self._scan_round_reset_key_by_market = {"us": "", "hk": "", "cn": ""}
            self._scan_round_in_day_by_market = {"us": 0, "hk": 0, "cn": 0}

    @staticmethod
    def _normalize_market(market: str) -> str:
        m = str(market or "").strip().lower()
        if m in {"us", "hk", "cn"}:
            return m
        return "us"

    @staticmethod
    def _market_reset_key(market: str) -> str:
        """
        计数周期 key（市场结束即清零）：
        - us: 20:00 ET 清零
        - hk/cn: 16:00 Asia/Shanghai 清零
        """
        mk = AutoTraderService._normalize_market(market)
        if mk == "us":
            now_et = datetime.now(timezone.utc).astimezone(_ET)
            day = now_et.date()
            if now_et.timetz().replace(tzinfo=None) < dt_time(20, 0):
                day = day - timedelta(days=1)
            return day.isoformat()

        now_bj = datetime.now(timezone.utc).astimezone(_BJ)
        day = now_bj.date()
        if now_bj.timetz().replace(tzinfo=None) < dt_time(16, 0):
            day = day - timedelta(days=1)
        return day.isoformat()

    def _persist_scan_counter(self) -> None:
        """原子写入扫描计数文件。"""
        try:
            payload = {
                "updated_at": datetime.now().isoformat(),
                "reset_key_by_market": dict(self._scan_round_reset_key_by_market),
                "count_by_market": dict(self._scan_round_in_day_by_market),
            }
            tmp = f"{AUTO_TRADER_SCAN_COUNTER_FILE}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, AUTO_TRADER_SCAN_COUNTER_FILE)
        except Exception:
            pass

    def _bump_scan_round_in_day(self, market: str) -> int:
        """
        当前 run_scan_once 调用时：按市场自增“今日第几次”并落盘。
        清零规则按市场结束时刻：
        - us: 20:00 ET
        - hk/cn: 16:00 北京时间
        """
        changed = False
        for m in ("us", "hk", "cn"):
            key_now = self._market_reset_key(m)
            if self._scan_round_reset_key_by_market.get(m) != key_now:
                self._scan_round_reset_key_by_market[m] = key_now
                self._scan_round_in_day_by_market[m] = 0
                changed = True

        mk = self._normalize_market(market)
        self._scan_round_in_day_by_market[mk] = int(self._scan_round_in_day_by_market.get(mk, 0) or 0) + 1
        changed = True

        if changed:
            self._persist_scan_counter()

        return int(self._scan_round_in_day_by_market.get(mk, 0) or 0)

    def _scan_round_by_market_snapshot(self) -> dict[str, int]:
        return {m: int(self._scan_round_in_day_by_market.get(m, 0) or 0) for m in ("us", "hk", "cn")}

    @staticmethod
    def _parse_iso_datetime_for_age(val: Any) -> Optional[datetime]:
        if val is None:
            return None
        s = str(val).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    @staticmethod
    def _sanitize_research_allocation_ctx(ctx: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not isinstance(ctx, dict):
            return None
        out = {k: v for k, v in ctx.items() if k != "weights"}
        w = ctx.get("weights")
        if isinstance(w, dict):
            out["symbol_count"] = len(w)
        return out

    def _build_research_allocation_context(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """读取 research 快照，构建本轮扫描可用的分配权重（仅买入数量裁剪用）。"""
        ctx: dict[str, Any] = {
            "enabled": False,
            "applied": False,
            "reason": "disabled",
            "weights": {},
            "meta": {},
        }
        if not bool(cfg.get("research_allocation_enabled")):
            return ctx
        ctx["enabled"] = True
        try:
            from api.auto_trader_research import get_research_snapshot, get_research_snapshot_history_result

            snap_id = str(cfg.get("research_allocation_snapshot_id") or "").strip()
            snap_market = str(cfg.get("market") or "us")
            if snap_id:
                snap = get_research_snapshot_history_result(
                    market=snap_market,
                    history_type="research",
                    snapshot_id=snap_id,
                )
                if not isinstance(snap, dict) or not snap:
                    snap = get_research_snapshot()
            else:
                snap = get_research_snapshot()
        except Exception:
            snap = {}
        if not isinstance(snap, dict) or not snap:
            ctx["reason"] = "no_snapshot"
            return ctx
        max_age = int(cfg.get("research_allocation_max_age_minutes", 0) or 0)
        if max_age > 0:
            dt = self._parse_iso_datetime_for_age(snap.get("generated_at"))
            if dt is None:
                ctx["reason"] = "invalid_generated_at"
                return ctx
            age_sec = (datetime.now() - dt).total_seconds()
            if age_sec > max_age * 60:
                ctx["reason"] = "snapshot_stale"
                ctx["meta"] = {"generated_at": snap.get("generated_at"), "max_age_minutes": max_age}
                return ctx
        snap_mkt = str(snap.get("market") or "").strip().lower()
        cfg_mkt = str(cfg.get("market") or "us").strip().lower()
        if snap_mkt and cfg_mkt and snap_mkt != cfg_mkt:
            ctx["reason"] = "market_mismatch"
            ctx["meta"] = {"snapshot_market": snap_mkt, "config_market": cfg_mkt}
            return ctx
        plan = snap.get("allocation_plan")
        if not isinstance(plan, list) or not plan:
            ctx["reason"] = "empty_plan"
            return ctx
        weights: dict[str, float] = {}
        for row in plan:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            try:
                w = float(row.get("weight") or 0.0)
            except Exception:
                w = 0.0
            if w > 0:
                weights[sym] = w
        if not weights:
            ctx["reason"] = "no_weights"
            return ctx
        ctx["applied"] = True
        ctx["reason"] = "ok"
        ctx["weights"] = weights
        rg = snap.get("regime_gating") if isinstance(snap.get("regime_gating"), dict) else {}
        ctx["meta"] = {
            "version": snap.get("version"),
            "generated_at": snap.get("generated_at"),
            "effective_exposure": rg.get("effective_exposure"),
        }
        return ctx

    def _resolve_buy_quantity(self, symbol: str, cfg: dict[str, Any]) -> int:
        q = self._quote_last(symbol) or {}
        price = float(q.get("last", 0) or 0)
        if price <= 0:
            return max(1, int(cfg.get("order_quantity", 100) or 100))
        try:
            account = self._get_account() or {}
        except Exception:
            account = {}
        base_qty: int
        try:
            qty = self._pipeline.size_order(
                symbol=symbol,
                price=price,
                account=account,
                bars_days=int(cfg.get("signal_bars_days", 90) or 90),
                kline=str(cfg.get("kline", "1d")),
                config=cfg,
            )
            if qty > 0:
                base_qty = int(qty)
            else:
                base_qty = max(1, int(cfg.get("order_quantity", 100) or 100))
        except Exception:
            base_qty = max(1, int(cfg.get("order_quantity", 100) or 100))

        # 统一的单标的市值上限裁剪：无论是否启用 research allocation，都先限制最大股数。
        try:
            max_pos = float(cfg.get("max_position_value", 50000) or 50000)
        except Exception:
            max_pos = 50000.0
        max_qty_pos = int(max_pos / price) if price > 0 else base_qty
        max_qty_pos = max(1, max_qty_pos)
        base_qty = max(1, min(base_qty, max_qty_pos))

        rctx = getattr(self, "_research_scan_ctx", None)
        if not isinstance(rctx, dict) or not rctx.get("applied"):
            return base_qty
        sym_u = str(symbol).strip().upper()
        w = rctx.get("weights", {}).get(sym_u) if isinstance(rctx.get("weights"), dict) else None
        if w is None:
            return base_qty
        try:
            equity = float(account.get("total_assets") or account.get("net_assets") or 0.0)
        except Exception:
            equity = 0.0
        if equity <= 0 or price <= 0:
            return base_qty
        try:
            scale = float(cfg.get("research_allocation_notional_scale", 1.0) or 1.0)
        except Exception:
            scale = 1.0
        scale = max(0.01, min(scale, 3.0))
        target_value = equity * float(w) * scale
        qty_res = int(target_value / price)
        if qty_res < 1:
            return base_qty
        qty_res = max(1, min(qty_res, max_qty_pos))
        return max(1, min(base_qty, qty_res))

    def _detect_signal(self, symbol: str, strategy_name: str, days: int, kline: str, target: str = "buy", relaxed_mode: bool = False) -> bool:
        bars = self._fetch_bars_for_scan(symbol, days, kline)
        if len(bars) < 25:
            return False
        sfn = get_strategy(strategy_name, None)
        now = sfn(bars, 0)
        if relaxed_mode:
            return now == target
        prev = sfn(bars[:-1], 0)
        return prev != target and now == target

    def _create_and_execute_signal(
        self,
        action: str,
        symbol: str,
        strategy_row: dict[str, Any],
        quantity: int,
        reason: str = "strong_stock_best_strategy_signal",
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """创建信号并立即执行（全自动模式）"""
        q = self._quote_last(symbol) or {}
        sid = f"AT-{uuid4().hex[:10].upper()}"
        now = datetime.now()
        
        signal = {
            "signal_id": sid,
            "status": "executing",  # 直接变为执行中
            "created_at": now.isoformat(),
            "executed_at": now.isoformat(),
            "symbol": symbol,
            "action": action,
            "quantity": int(quantity),
            "suggested_price": q.get("last"),
            "market_change_pct": q.get("change_pct"),
            "strategy": strategy_row.get("strategy"),
            "strategy_label": strategy_row.get("strategy_label"),
            "strategy_score": strategy_row.get("composite_score"),
            "reason": reason,
            "auto_executed": True,
        }
        sp0 = strategy_row.get("strategy_params")
        if isinstance(sp0, dict) and sp0:
            signal["strategy_params"] = dict(sp0)
        src0 = strategy_row.get("scoring_source")
        if isinstance(src0, str) and src0.strip():
            signal["scoring_source"] = src0.strip()
        if extra:
            signal.update(extra)
        
        with self._lock:
            self._signals[sid] = signal
        
        # 立即执行交易
        exec_result = self._execute_trade_with_risk_control(action, symbol, quantity, strategy_row)
        
        # 更新信号状态
        if exec_result.get("success"):
            signal["status"] = "executed"
            signal["execution_result"] = exec_result
        else:
            signal["status"] = "failed"
            signal["error"] = exec_result.get("error")
        
        signal["updated_at"] = datetime.now().isoformat()
        
        # 发送通知
        self._push_execution_result(signal, exec_result)
        
        return signal

    def _create_simulated_signal(
        self,
        action: str,
        symbol: str,
        strategy_row: dict[str, Any],
        quantity: int,
        reason: str = "dry_run_signal",
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        q = self._quote_last(symbol) or {}
        sid = f"AT-{uuid4().hex[:10].upper()}"
        now = datetime.now()
        signal = {
            "signal_id": sid,
            "status": "simulated",
            "created_at": now.isoformat(),
            "executed_at": now.isoformat(),
            "symbol": symbol,
            "action": action,
            "quantity": int(quantity),
            "suggested_price": q.get("last"),
            "market_change_pct": q.get("change_pct"),
            "strategy": strategy_row.get("strategy"),
            "strategy_label": strategy_row.get("strategy_label"),
            "strategy_score": strategy_row.get("composite_score"),
            "reason": reason,
            "auto_executed": False,
            "dry_run": True,
            "note": "只读演练模式，未真实下单",
        }
        sp1 = strategy_row.get("strategy_params")
        if isinstance(sp1, dict) and sp1:
            signal["strategy_params"] = dict(sp1)
        src1 = strategy_row.get("scoring_source")
        if isinstance(src1, str) and src1.strip():
            signal["scoring_source"] = src1.strip()
        if extra:
            signal.update(extra)
        with self._lock:
            self._signals[sid] = signal
        return signal

    def _push_execution_result(self, signal: dict[str, Any], exec_result: dict[str, Any]) -> bool:
        """推送执行结果到飞书"""
        try:
            from api.notification_preferences import should_send_full_auto_execution

            if not should_send_full_auto_execution(success=bool(exec_result.get("success"))):
                return False
        except Exception:
            pass
        if exec_result.get("success"):
            trade = exec_result.get("trade_record", {})
            text = (
                "[AutoTrader 全自动交易执行成功]\n"
                f"信号ID: {signal['signal_id']}\n"
                f"标的: {signal['symbol']}\n"
                f"方向: {'买入' if signal.get('action') == 'buy' else '卖出'}\n"
                f"数量: {signal['quantity']} 股\n"
                f"成交价: ${trade.get('price', '-'):.2f}\n"
                f"成交金额: ${trade.get('value', 0):.2f}\n"
                f"策略: {signal.get('strategy_label') or signal.get('strategy')}\n"
                f"策略评分: {signal.get('strategy_score')}\n"
                f"风控检查: 通过\n"
                f"执行时间: {signal['executed_at']}"
            )
        else:
            text = (
                "[AutoTrader 全自动交易执行失败]\n"
                f"信号ID: {signal['signal_id']}\n"
                f"标的: {signal['symbol']}\n"
                f"方向: {'买入' if signal.get('action') == 'buy' else '卖出'} {signal['quantity']} 股\n"
                f"策略: {signal.get('strategy_label') or signal.get('strategy')}\n"
                f"失败原因: {exec_result.get('error', '未知错误')}\n"
                f"时间: {signal['created_at']}"
            )
        return self._send_feishu(text)

    def pair_portfolio_backtest(
        self,
        market: str = "us",
        days: int = 180,
        kline: str = "1d",
        initial_capital: float = 100000.0,
    ) -> dict[str, Any]:
        with self._lock:
            cfg = dict(self._config)
        return run_pair_portfolio_backtest(
            fetch_bars=self._fetch_bars,
            pair_pool=normalize_pair_pool(cfg.get("pair_pool")),
            market=market,
            strategies=list(cfg.get(
                "strategies",
                list_strategy_names(),
            )),
            days=days,
            kline=kline,
            initial_capital=initial_capital,
            max_single_ratio=0.2,
            max_total_ratio=0.5,
        )

    def _should_auto_execute(self) -> bool:
        """检查是否应该自动执行"""
        with self._lock:
            cfg = dict(self._config)
        return cfg.get("auto_execute", True)

    def _can_open_symbol_now(self, symbol: str, cfg: dict[str, Any], action: str = "buy") -> tuple[bool, str]:
        """防重单保护：统一走 Guard 管线。"""
        ok, reason, _ = self._can_open_symbol_now_verbose(symbol, cfg, action)
        return ok, reason

    def _can_open_symbol_now_verbose(
        self, symbol: str, cfg: dict[str, Any], action: str = "buy"
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        """防重单保护（返回详细链路）。"""
        sym_u = str(symbol).upper().strip()
        act_l = str(action).lower().strip()
        with self._lock:
            trades = list(self._executed_trades)
            # 将当前内存中的 pending/executing 信号也纳入“已执行”集合，
            # 用于冷却/每日次数限制，避免 worker 重启后重复生成同标的待确认单。
            for s in self._signals.values():
                st = str(s.get("status", "") or "").lower().strip()
                if st not in {"pending", "executing"}:
                    continue
                s_sym = str(s.get("symbol", "") or "").upper().strip()
                if s_sym != sym_u:
                    continue
                s_act = str(s.get("action", "") or "").lower().strip()
                if s_act != act_l:
                    continue
                ts = s.get("executed_at") or s.get("created_at") or s.get("updated_at")
                if not ts:
                    continue
                trades.append({"symbol": s_sym, "action": s_act, "executed_at": ts})
        try:
            positions = self._get_positions() or {}
        except Exception:
            positions = {}
        return self._pipeline.check_guards_verbose(
            symbol=symbol,
            action=action,
            config=cfg,
            executed_trades=trades,
            positions=positions,
        )

    def _check_portfolio_risk_budget(
        self,
        cfg: dict[str, Any],
        action: str,
        scan_created: dict[str, int],
    ) -> tuple[bool, str]:
        """组合风险预算：限制同向集中与并发持仓。"""
        if str(action).lower() != "buy":
            return True, ""
        max_new_buys = int(cfg.get("same_direction_max_new_orders_per_scan", 2) or 0)
        if max_new_buys > 0 and int(scan_created.get("buy", 0)) >= max_new_buys:
            return False, f"same_direction_new_order_limit:{max_new_buys}"
        max_longs = int(cfg.get("max_concurrent_long_positions", 8) or 0)
        if max_longs > 0:
            try:
                positions = self._get_positions() or {}
                active_longs = [
                    p
                    for p in positions.get("positions", [])
                    if float(p.get("quantity", 0) or 0) > 0
                ]
                if len(active_longs) >= max_longs:
                    return False, f"max_concurrent_long_positions:{max_longs}"
            except Exception:
                return True, ""
        return True, ""

    def _build_no_signal_suggestions(self, summary: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
        skipped = summary.get("skipped", {}) or {}
        suggestions: list[str] = []
        if int(skipped.get("score_error", 0) or 0) > 0:
            suggestions.append("检查评分失败标的，清理无效代码；可保留“自动剔除无效代码”为开启。")
        if int(skipped.get("no_signal", 0) or 0) > 0:
            if not bool(cfg.get("signal_relaxed_mode", False)):
                suggestions.append("可开启“宽松信号模式”，由“新触发 buy”改为“当前为 buy 即触发”。")
            suggestions.append("可缩短K线周期（如 1h -> 30m/5m/1m）或扩大 top_n，提升信号出现概率。")
        if int(summary.get("strong_count", 0) or 0) <= 0:
            suggestions.append("当前未筛出强势股，建议扩充股票池或切换市场。")
        if bool(cfg.get("pair_mode")):
            suggestions.append("若配对模式信号偏少，可临时关闭配对模式验证单票流程。")
        active_template = str(cfg.get("active_template", "custom"))
        if int(skipped.get("no_signal", 0) or 0) > 0:
            if active_template == "trend":
                suggestions.append("当前模板为趋势，若行情震荡可切到“均值回归”模板。")
            elif active_template == "mean_reversion":
                suggestions.append("当前模板为均值回归，若行情单边可切到“趋势”模板。")
            elif active_template == "defensive":
                suggestions.append("防守模板信号较少属正常，若想提高频率可切到“趋势”模板。")
            else:
                suggestions.append("可尝试模板切换：趋势 -> 均值回归，观察信号覆盖变化。")
        return suggestions[:4]

    def _push_observer_hint_if_needed(self, summary: dict[str, Any], cfg: dict[str, Any]) -> bool:
        """连续N轮无信号时推送观察模式提示。"""
        if not bool(cfg.get("observer_mode_enabled", True)):
            with self._lock:
                self._consecutive_no_signal_rounds = 0
            return False

        created = int(summary.get("created_signals", 0) or 0)
        if created > 0:
            with self._lock:
                self._consecutive_no_signal_rounds = 0
            return False

        with self._lock:
            self._consecutive_no_signal_rounds += 1
            rounds = self._consecutive_no_signal_rounds

        threshold = max(1, int(cfg.get("observer_no_signal_rounds", 3) or 3))
        if rounds < threshold:
            return False
        # 达阈值后按 N 轮节奏重复提醒，避免每轮刷屏。
        if rounds % threshold != 0:
            return False

        skipped = summary.get("skipped", {}) or {}
        strong = summary.get("strong_stocks", []) or []
        top_text = ", ".join(
            f"{x.get('symbol')}({x.get('strength_score', '-')})"
            for x in strong[:5]
            if x.get("symbol")
        ) or "无"
        suggestions = self._build_no_signal_suggestions(summary, cfg)
        suggest_text = "\n".join(f"- {s}" for s in suggestions) if suggestions else "- 维持当前参数，等待下一轮信号。"
        invalids = summary.get("invalid_symbol_errors", []) or []
        invalid_text = "\n".join(f"- {e}" for e in invalids[:3]) if invalids else "- 无"

        try:
            from api.notification_preferences import should_send_observer_digest

            if not should_send_observer_digest():
                return False
        except Exception:
            pass

        text = (
            "[AutoTrader 观察模式提示]\n"
            f"连续无信号轮数: {rounds} (阈值: {threshold})\n"
            f"扫描模式: {'ETF配对' if cfg.get('pair_mode') else '强势股'} | 交易模式: {'全自动' if cfg.get('auto_execute') else '半自动'}\n"
            f"本轮强势股数量: {summary.get('strong_count', 0)}\n"
            f"跳过统计: 无信号 {skipped.get('no_signal', 0)} / 评分失败 {skipped.get('score_error', 0)} / 已有活跃信号 {skipped.get('has_active_signal', 0)} / 异常 {skipped.get('exception', 0)}\n"
            f"强势股Top: {top_text}\n"
            "无效代码摘要:\n"
            f"{invalid_text}\n"
            "建议参数调整:\n"
            f"{suggest_text}"
        )
        ok = self._send_feishu(text)
        if ok:
            with self._lock:
                self._last_observer_push_at = datetime.now().isoformat()
        return ok

    def run_scan_once(self) -> dict[str, Any]:
        """执行一次扫描（全自动模式）"""
        started = time.perf_counter()
        with self._lock:
            cfg = dict(self._config)
        self._research_scan_ctx = self._build_research_allocation_context(cfg)
        try:
            return self._run_scan_once_inner(cfg, started)
        finally:
            self._last_research_allocation_ctx = self._sanitize_research_allocation_ctx(self._research_scan_ctx)
            self._research_scan_ctx = None
            self._scan_bars_cache.clear()

    def _fetch_bars_for_scan(self, symbol: str, days: int, kline: str) -> list[Bar]:
        key = (str(symbol or "").strip().upper(), int(days), str(kline))
        cached = self._scan_bars_cache.get(key)
        if cached is not None:
            return cached
        bars = self._fetch_bars(symbol, days, kline)
        self._scan_bars_cache[key] = bars
        return bars

    def _run_scan_once_inner(self, cfg: dict[str, Any], started: float) -> dict[str, Any]:
        # 重置每日计数器
        self._reset_daily_counter_if_needed()
        current_market = self._normalize_market(str(cfg.get("market", "us") or "us"))
        scan_round_in_day = self._bump_scan_round_in_day(current_market)
        scan_round_in_day_by_market = self._scan_round_by_market_snapshot()

        created: list[dict[str, Any]] = []
        executed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        skipped_score_error = 0
        skipped_no_signal = 0
        skipped_has_active = 0
        skipped_exception = 0
        skipped_duplicate_guard = 0
        skipped_no_position = 0
        skipped_ml_filter = 0
        invalid_symbol_errors: list[str] = []
        pruned_invalid_symbols: list[str] = []
        decision_log: list[dict[str, Any]] = []
        scan_created_by_side: dict[str, int] = {"buy": 0, "sell": 0}
        
        pair_mode_enabled = bool(cfg.get("pair_mode"))
        strong = self.screen_strong_stocks(cfg["market"], cfg["top_n"], cfg["kline"])
        auto_execute = self._should_auto_execute()
        pair_mode_allow_auto_execute = bool(cfg.get("pair_mode_allow_auto_execute", False))
        pair_mode_execution_forced_off = False
        # 默认安全策略：ETF 配对模式不做自动下单，除非显式打开高级开关。
        if pair_mode_enabled and auto_execute and not pair_mode_allow_auto_execute:
            auto_execute = False
            pair_mode_execution_forced_off = True
        dry_run_mode = bool(cfg.get("dry_run_mode", False))

        if pair_mode_enabled:
            # Pair模式：获取配对股票列表进行扫描
            pair_symbols = self._get_pair_symbols(cfg["market"])
            for sym in pair_symbols:
                # 对配对股票进行策略评分和信号检测
                entry_decision = None
                try:
                    scored = self.score_strategies(
                        symbol=sym,
                        strategies=list(cfg["strategies"]),
                        days=int(cfg["backtest_days"]),
                        kline=cfg["kline"],
                        strategy_params_map=cfg.get("strategy_params_map")
                        if isinstance(cfg.get("strategy_params_map"), dict)
                        else None,
                        cfg=cfg,
                    )
                    if not scored or scored[0].get("error"):
                        skipped_score_error += 1
                        err = str(scored[0].get("error", "")) if scored else ""
                        if "invalid symbol" in err.lower():
                            invalid_symbol_errors.append(f"{sym}: {err}")
                            if cfg.get("auto_prune_invalid_symbols", True) and self._prune_invalid_symbol(sym, cfg["market"]):
                                pruned_invalid_symbols.append(sym)
                        continue
                    best = scored[0]
                    entry_decision = self._pipeline.evaluate_entry(
                        ScanContext(
                            symbol=sym,
                            strategy_name=best["strategy"],
                            bars_days=int(cfg["signal_bars_days"]),
                            kline=cfg["kline"],
                            relaxed_mode=bool(cfg.get("signal_relaxed_mode", False)),
                            strategy_params=_strategy_params_for_scan_context(best),
                        )
                    )
                    hit = entry_decision.should_enter
                    if hit:
                        ml_pass, ml_info = self._ml_passes_for_buy(sym, cfg)
                        if not ml_pass:
                            skipped_ml_filter += 1
                            decision_log.append(
                                {
                                    "symbol": sym,
                                    "side": "buy",
                                    "result": "skipped",
                                    "reason": "ml_filter_block",
                                    "trace": {"entry": entry_decision.reason, "ml_filter": ml_info},
                                }
                            )
                            continue

                        # Pair 模式下也要做“已有活跃信号”拦截（否则 worker 重启后会不断重复创建 pending）
                        now = datetime.now()
                        with self._lock:
                            has_active = any(
                                str(s.get("symbol", "")).upper().strip() == str(sym).upper().strip()
                                and str(s.get("action", "")).lower().strip() == "buy"
                                and str(s.get("status", "")).lower().strip() in {"pending", "executing"}
                                and (
                                    # pending/executing 过期了就不算“活跃”
                                    (lambda exp: exp is None or now <= exp)(_safe_parse_iso_datetime(s.get("expires_at")))
                                )
                                for s in self._signals.values()
                            )
                        if has_active:
                            skipped_has_active += 1
                            decision_log.append(
                                {"symbol": sym, "side": "buy", "result": "skipped", "reason": "has_active_signal"}
                            )
                            continue

                        can_open, guard_reason, guard_trace = self._can_open_symbol_now_verbose(sym, cfg, action="buy")
                        if not can_open:
                            skipped_duplicate_guard += 1
                            decision_log.append(
                                {
                                    "symbol": sym,
                                    "side": "buy",
                                    "result": "skipped",
                                    "reason": f"guard_block:{guard_reason}",
                                    "trace": {"entry": entry_decision.reason, "guards": guard_trace},
                                }
                            )
                            continue
                        budget_ok, budget_reason = self._check_portfolio_risk_budget(
                            cfg=cfg, action="buy", scan_created=scan_created_by_side
                        )
                        if not budget_ok:
                            skipped_duplicate_guard += 1
                            decision_log.append(
                                {
                                    "symbol": sym,
                                    "side": "buy",
                                    "result": "skipped",
                                    "reason": f"guard_block:{budget_reason}",
                                    "trace": {"entry": entry_decision.reason, "guards": guard_trace},
                                }
                            )
                            continue
                        buy_qty = self._resolve_buy_quantity(sym, cfg)
                        if auto_execute and not dry_run_mode:
                            signal = self._create_and_execute_signal(
                                "buy",
                                sym,
                                best,
                                buy_qty,
                                extra={
                                    "trace": {
                                        "entry_rule": entry_decision.metadata.get("entry_rule", "strategy_cross"),
                                        "entry_reason": entry_decision.reason,
                                        "ml_filter": ml_info,
                                        "guards": guard_trace,
                                    }
                                },
                            )
                            created.append(signal)
                            scan_created_by_side["buy"] = int(scan_created_by_side.get("buy", 0)) + 1
                            if signal["status"] == "executed":
                                executed.append(signal)
                            else:
                                failed.append(signal)
                            decision_log.append(
                                {
                                    "symbol": sym,
                                    "side": "buy",
                                    "result": signal["status"],
                                    "reason": signal.get("reason", ""),
                                    "signal_id": signal.get("signal_id"),
                                    "trace": signal.get("trace", {}),
                                }
                            )
                        elif dry_run_mode:
                            signal = self._create_simulated_signal(
                                "buy",
                                sym,
                                best,
                                buy_qty,
                                reason="dry_run_buy_signal",
                                extra={
                                    "trace": {
                                        "entry_rule": entry_decision.metadata.get("entry_rule", "strategy_cross"),
                                        "entry_reason": entry_decision.reason,
                                        "ml_filter": ml_info,
                                        "guards": guard_trace,
                                    }
                                },
                            )
                            created.append(signal)
                            scan_created_by_side["buy"] = int(scan_created_by_side.get("buy", 0)) + 1
                            decision_log.append(
                                {
                                    "symbol": sym,
                                    "side": "buy",
                                    "result": "simulated",
                                    "reason": "dry_run_mode",
                                    "signal_id": signal.get("signal_id"),
                                    "trace": signal.get("trace", {}),
                                }
                            )
                        else:
                            signal = self._create_pending_signal(
                                sym,
                                best,
                                buy_qty,
                                action="buy",
                                extra={
                                    "trace": {
                                        "entry_rule": entry_decision.metadata.get("entry_rule", "strategy_cross"),
                                        "entry_reason": entry_decision.reason,
                                        "ml_filter": ml_info,
                                        "guards": guard_trace,
                                    }
                                },
                            )
                            created.append(signal)
                            scan_created_by_side["buy"] = int(scan_created_by_side.get("buy", 0)) + 1
                            decision_log.append(
                                {
                                    "symbol": sym,
                                    "side": "buy",
                                    "result": "pending",
                                    "reason": signal.get("reason", ""),
                                    "signal_id": signal.get("signal_id"),
                                    "trace": signal.get("trace", {}),
                                }
                            )
                    else:
                        skipped_no_signal += 1
                        decision_log.append(
                            {
                                "symbol": sym,
                                "side": "buy",
                                "result": "skipped",
                                "reason": f"entry_miss:{entry_decision.reason}",
                                "trace": {"entry": entry_decision.reason},
                            }
                        )
                except Exception:
                    skipped_exception += 1
                    continue
        else:
            for row in strong:
                sym = row["symbol"]
                entry_decision = None
                
                # 检查是否已有活跃信号
                with self._lock:
                    has_active = any(
                        s.get("symbol") == sym and s.get("status") in ["pending", "executing", "executed"]
                        for s in self._signals.values()
                    )
                if has_active:
                    skipped_has_active += 1
                    decision_log.append(
                        {"symbol": sym, "side": "buy", "result": "skipped", "reason": "has_active_signal"}
                    )
                    continue
                
                scored = self.score_strategies(
                    symbol=sym,
                    strategies=list(cfg["strategies"]),
                    days=int(cfg["backtest_days"]),
                    kline=cfg["kline"],
                    strategy_params_map=cfg.get("strategy_params_map")
                    if isinstance(cfg.get("strategy_params_map"), dict)
                    else None,
                    cfg=cfg,
                )
                if not scored:
                    skipped_score_error += 1
                    decision_log.append(
                        {"symbol": sym, "side": "buy", "result": "skipped", "reason": "score_empty"}
                    )
                    continue
                best = scored[0]
                if best.get("error"):
                    skipped_score_error += 1
                    err = str(best.get("error", ""))
                    if "invalid symbol" in err.lower():
                        invalid_symbol_errors.append(f"{sym}: {err}")
                        if cfg.get("auto_prune_invalid_symbols", True) and self._prune_invalid_symbol(sym, cfg["market"]):
                            pruned_invalid_symbols.append(sym)
                    continue
                try:
                    entry_decision = self._pipeline.evaluate_entry(
                        ScanContext(
                            symbol=sym,
                            strategy_name=best["strategy"],
                            bars_days=int(cfg["signal_bars_days"]),
                            kline=cfg["kline"],
                            relaxed_mode=bool(cfg.get("signal_relaxed_mode", False)),
                            strategy_params=_strategy_params_for_scan_context(best),
                        )
                    )
                    hit = entry_decision.should_enter
                except Exception:
                    hit = False
                if not hit:
                    skipped_no_signal += 1
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "buy",
                            "result": "skipped",
                            "reason": f"entry_miss:{entry_decision.reason if entry_decision else 'exception'}",
                        }
                    )
                    continue
                ml_pass, ml_info = self._ml_passes_for_buy(sym, cfg)
                if not ml_pass:
                    skipped_ml_filter += 1
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "buy",
                            "result": "skipped",
                            "reason": "ml_filter_block",
                            "trace": {"entry": entry_decision.reason if entry_decision else "", "ml_filter": ml_info},
                        }
                    )
                    continue
                can_open, guard_reason, guard_trace = self._can_open_symbol_now_verbose(sym, cfg, action="buy")
                if not can_open:
                    skipped_duplicate_guard += 1
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "buy",
                            "result": "skipped",
                            "reason": f"guard_block:{guard_reason}",
                            "trace": {"guards": guard_trace, "entry": entry_decision.reason},
                        }
                    )
                    continue
                budget_ok, budget_reason = self._check_portfolio_risk_budget(
                    cfg=cfg, action="buy", scan_created=scan_created_by_side
                )
                if not budget_ok:
                    skipped_duplicate_guard += 1
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "buy",
                            "result": "skipped",
                            "reason": f"guard_block:{budget_reason}",
                            "trace": {"guards": guard_trace, "entry": entry_decision.reason},
                        }
                    )
                    continue
                buy_qty = self._resolve_buy_quantity(sym, cfg)
                
                if auto_execute and not dry_run_mode:
                    # 全自动模式：直接执行
                    signal = self._create_and_execute_signal(
                        "buy",
                        sym,
                        best,
                        buy_qty,
                        extra={
                            "trace": {
                                "entry_rule": entry_decision.metadata.get("entry_rule", "strategy_cross"),
                                "entry_reason": entry_decision.reason,
                                "ml_filter": ml_info,
                                "guards": guard_trace,
                            }
                        },
                    )
                    created.append(signal)
                    scan_created_by_side["buy"] = int(scan_created_by_side.get("buy", 0)) + 1
                    if signal["status"] == "executed":
                        executed.append(signal)
                    else:
                        failed.append(signal)
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "buy",
                            "result": signal["status"],
                            "reason": signal.get("reason", ""),
                            "signal_id": signal.get("signal_id"),
                            "trace": signal.get("trace", {}),
                        }
                    )
                elif dry_run_mode:
                    signal = self._create_simulated_signal(
                        "buy",
                        sym,
                        best,
                        buy_qty,
                        reason="dry_run_buy_signal",
                        extra={
                            "trace": {
                                "entry_rule": entry_decision.metadata.get("entry_rule", "strategy_cross"),
                                "entry_reason": entry_decision.reason,
                                "ml_filter": ml_info,
                                "guards": guard_trace,
                            }
                        },
                    )
                    created.append(signal)
                    scan_created_by_side["buy"] = int(scan_created_by_side.get("buy", 0)) + 1
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "buy",
                            "result": "simulated",
                            "reason": "dry_run_mode",
                            "signal_id": signal.get("signal_id"),
                            "trace": signal.get("trace", {}),
                        }
                    )
                else:
                    # 半自动模式：创建待确认信号（兼容旧模式）
                    signal = self._create_pending_signal(
                        sym,
                        best,
                        buy_qty,
                        action="buy",
                        extra={
                            "trace": {
                                "entry_rule": entry_decision.metadata.get("entry_rule", "strategy_cross"),
                                "entry_reason": entry_decision.reason,
                                "ml_filter": ml_info,
                                "guards": guard_trace,
                            }
                        },
                    )
                    created.append(signal)
                    scan_created_by_side["buy"] = int(scan_created_by_side.get("buy", 0)) + 1
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "buy",
                            "result": "pending",
                            "reason": signal.get("reason", ""),
                            "signal_id": signal.get("signal_id"),
                            "trace": signal.get("trace", {}),
                        }
                    )

        # 自动卖出链路：只针对已有持仓标的
        if bool(cfg.get("auto_sell_enabled", False)):
            try:
                positions = self._get_positions() or {}
                held_rows = [p for p in positions.get("positions", []) if float(p.get("quantity", 0) or 0) > 0]
            except Exception:
                held_rows = []
            for p in held_rows:
                sym = str(p.get("symbol", "")).upper().strip()
                exit_decision = None
                qty_held = int(float(p.get("quantity", 0) or 0))
                if not sym or qty_held <= 0:
                    skipped_no_position += 1
                    continue
                scored = self.score_strategies(
                    symbol=sym,
                    strategies=list(cfg["strategies"]),
                    days=int(cfg["backtest_days"]),
                    kline=cfg["kline"],
                    strategy_params_map=cfg.get("strategy_params_map")
                    if isinstance(cfg.get("strategy_params_map"), dict)
                    else None,
                    cfg=cfg,
                )
                if not scored:
                    skipped_score_error += 1
                    decision_log.append(
                        {"symbol": sym, "side": "sell", "result": "skipped", "reason": "score_empty"}
                    )
                    continue
                best = scored[0]
                if best.get("error"):
                    skipped_score_error += 1
                    decision_log.append(
                        {"symbol": sym, "side": "sell", "result": "skipped", "reason": "score_error"}
                    )
                    continue
                try:
                    current_price = float(p.get("current_price", 0) or 0)
                    avg_cost = float(p.get("cost_price", 0) or 0)
                    opened_at_raw = p.get("opened_at")
                    opened_at = None
                    if opened_at_raw:
                        try:
                            opened_at = datetime.fromisoformat(str(opened_at_raw))
                        except Exception:
                            opened_at = None
                    exit_decision = self._pipeline.evaluate_exit(
                        ScanContext(
                            symbol=sym,
                            strategy_name=best["strategy"],
                            bars_days=int(cfg["signal_bars_days"]),
                            kline=cfg["kline"],
                            relaxed_mode=bool(cfg.get("signal_relaxed_mode", False)),
                            strategy_params=_strategy_params_for_scan_context(best),
                        ),
                        PositionSnapshot(
                            symbol=sym,
                            quantity=qty_held,
                            avg_cost=avg_cost,
                            current_price=current_price,
                            opened_at=opened_at,
                        ),
                    )
                    sell_hit = exit_decision.should_exit
                except Exception:
                    sell_hit = False
                if not sell_hit:
                    skipped_no_signal += 1
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "sell",
                            "result": "skipped",
                            "reason": f"exit_miss:{exit_decision.reason if exit_decision else 'exception'}",
                            "trace": (exit_decision.metadata.get("exit_trace", []) if exit_decision else []),
                        }
                    )
                    continue
                can_sell, guard_reason, guard_trace = self._can_open_symbol_now_verbose(sym, cfg, action="sell")
                if not can_sell:
                    skipped_duplicate_guard += 1
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "sell",
                            "result": "skipped",
                            "reason": f"guard_block:{guard_reason}",
                            "trace": {"exit": exit_decision.metadata.get("exit_trace", []), "guards": guard_trace},
                        }
                    )
                    continue

                if bool(cfg.get("sell_full_position", True)):
                    sell_qty = qty_held
                else:
                    sell_qty = min(qty_held, max(1, int(cfg.get("sell_order_quantity", 100) or 100)))
                if sell_qty <= 0:
                    skipped_no_position += 1
                    continue

                if auto_execute and not dry_run_mode:
                    signal = self._create_and_execute_signal(
                        "sell",
                        sym,
                        best,
                        int(sell_qty),
                        reason="position_best_strategy_sell_signal",
                        extra={
                            "trace": {
                                "exit_reason": exit_decision.reason,
                                "exit_rules": exit_decision.metadata.get("exit_trace", []),
                                "guards": guard_trace,
                            }
                        },
                    )
                    created.append(signal)
                    if signal["status"] == "executed":
                        executed.append(signal)
                    else:
                        failed.append(signal)
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "sell",
                            "result": signal["status"],
                            "reason": signal.get("reason", ""),
                            "signal_id": signal.get("signal_id"),
                            "trace": signal.get("trace", {}),
                        }
                    )
                elif dry_run_mode:
                    signal = self._create_simulated_signal(
                        "sell",
                        sym,
                        best,
                        int(sell_qty),
                        reason="dry_run_sell_signal",
                        extra={
                            "trace": {
                                "exit_reason": exit_decision.reason,
                                "exit_rules": exit_decision.metadata.get("exit_trace", []),
                                "guards": guard_trace,
                            }
                        },
                    )
                    created.append(signal)
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "sell",
                            "result": "simulated",
                            "reason": "dry_run_mode",
                            "signal_id": signal.get("signal_id"),
                            "trace": signal.get("trace", {}),
                        }
                    )
                else:
                    signal = self._create_pending_signal(
                        sym,
                        best,
                        int(sell_qty),
                        reason="position_best_strategy_sell_signal",
                        action="sell",
                        extra={
                            "trace": {
                                "exit_reason": exit_decision.reason,
                                "exit_rules": exit_decision.metadata.get("exit_trace", []),
                                "guards": guard_trace,
                            }
                        },
                    )
                    created.append(signal)
                    decision_log.append(
                        {
                            "symbol": sym,
                            "side": "sell",
                            "result": "pending",
                            "reason": signal.get("reason", ""),
                            "signal_id": signal.get("signal_id"),
                            "trace": signal.get("trace", {}),
                        }
                    )

        rscan = self._research_scan_ctx if isinstance(self._research_scan_ctx, dict) else {}
        summary = {
            "scan_time": datetime.now().isoformat(),
            "scan_round_in_day": scan_round_in_day,
            "scan_round_in_day_by_market": scan_round_in_day_by_market,
            "scan_round_market": current_market,
            "strong_count": len(strong),
            "pair_mode": pair_mode_enabled,
            "auto_execute": auto_execute,
            "dry_run_mode": dry_run_mode,
            "pair_mode_execution_forced_off": pair_mode_execution_forced_off,
            "research_allocation": {
                "config_enabled": bool(cfg.get("research_allocation_enabled")),
                "scan_applied": bool(rscan.get("applied")),
                "scan_reason": str(rscan.get("reason") or ""),
                "snapshot_meta": rscan.get("meta") if isinstance(rscan.get("meta"), dict) else {},
                "weights_symbol_count": len(rscan.get("weights") or {}) if isinstance(rscan.get("weights"), dict) else 0,
            },
            "created_signals": len(created),
            "created_by_side": scan_created_by_side,
            "executed_signals": len(executed),
            "failed_signals": len(failed),
            "daily_trade_count": self._daily_trade_count,
            "created": created,
            "executed": executed,
            "failed": failed,
            "strong_stocks": strong,
            "skipped": {
                "score_error": skipped_score_error,
                "no_signal": skipped_no_signal,
                "has_active_signal": skipped_has_active,
                "exception": skipped_exception,
                "duplicate_guard": skipped_duplicate_guard,
                "ml_filter": skipped_ml_filter,
                "no_position": skipped_no_position,
            },
            "invalid_symbol_errors": invalid_symbol_errors[:20],
            "pruned_invalid_symbols": sorted(set(pruned_invalid_symbols)),
            "decision_log": decision_log[:80],
        }
        observer_sent = self._push_observer_hint_if_needed(summary, cfg)
        summary["observer_hint_sent"] = observer_sent
        summary["consecutive_no_signal_rounds"] = self._consecutive_no_signal_rounds
        with self._lock:
            self._last_scan_at = summary["scan_time"]
            self._last_scan_summary = summary
        emit_metric(
            event="auto_trader.run_scan_once",
            ok=True,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            tags={"market": str(cfg.get("market", "us")), "pair_mode": bool(cfg.get("pair_mode", False))},
            extra={
                "strong_count": int(summary.get("strong_count", 0) or 0),
                "created_signals": int(summary.get("created_signals", 0) or 0),
                "executed_signals": int(summary.get("executed_signals", 0) or 0),
            },
        )
        return summary

    def _create_pending_signal(
        self,
        symbol: str,
        strategy_row: dict[str, Any],
        quantity: int,
        reason: str = "strong_stock_best_strategy_signal",
        action: str = "buy",
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """创建待确认信号（半自动模式兼容）"""
        q = self._quote_last(symbol) or {}
        sid = f"AT-{uuid4().hex[:10].upper()}"
        now = datetime.now()
        signal = {
            "signal_id": sid,
            "status": "pending",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=6)).isoformat(),
            "symbol": symbol,
            "action": action,
            "quantity": int(quantity),
            "suggested_price": q.get("last"),
            "market_change_pct": q.get("change_pct"),
            "strategy": strategy_row.get("strategy"),
            "strategy_label": strategy_row.get("strategy_label"),
            "strategy_score": strategy_row.get("composite_score"),
            "reason": reason,
            "auto_executed": False,
        }
        sp2 = strategy_row.get("strategy_params")
        if isinstance(sp2, dict) and sp2:
            signal["strategy_params"] = dict(sp2)
        src2 = strategy_row.get("scoring_source")
        if isinstance(src2, str) and src2.strip():
            signal["scoring_source"] = src2.strip()
        if extra:
            signal.update(extra)
        
        with self._lock:
            self._signals[sid] = signal
        
        # 发送待确认通知
        self._push_pending_signal(signal)
        # 落盘，API/前端读取时能看到 Worker 生成的 pending 信号
        self._persist_signals_to_disk()
        
        return signal

    def _push_pending_signal(self, signal: dict[str, Any]) -> bool:
        """推送待确认信号（半自动模式）"""
        enabled = False
        try:
            from api.notification_preferences import should_send_semi_auto_pending

            enabled = bool(should_send_semi_auto_pending())
        except Exception:
            enabled = False

        signal["feishu_semi_auto_pending_enabled"] = enabled
        if not enabled:
            signal["feishu_semi_auto_pending_sent"] = False
            signal["feishu_semi_auto_pending_ok"] = False
            return False
        text = (
            "[AutoTrader 待确认下单 - 半自动模式]\n"
            f"信号ID: {signal['signal_id']}\n"
            f"标的: {signal['symbol']}\n"
            f"方向: {'买入' if signal.get('action') == 'buy' else '卖出'}\n"
            f"数量: {signal['quantity']} 股\n"
            f"建议价: {signal.get('suggested_price', '-')}\n"
            f"策略: {signal.get('strategy_label') or signal.get('strategy')}\n"
            f"策略评分: {signal.get('strategy_score')}\n"
            f"有效期至: {signal['expires_at']}\n\n"
            "请在 UI 调用确认接口执行下单。"
        )
        ok = bool(self._send_feishu(text))
        signal["feishu_semi_auto_pending_sent"] = True
        signal["feishu_semi_auto_pending_ok"] = ok
        signal["feishu_semi_auto_pending_at"] = datetime.now().isoformat()
        return ok

    def confirm_and_execute(self, signal_id: str) -> dict[str, Any]:
        """手动确认并执行信号（半自动模式使用）"""
        with self._lock:
            s = self._signals.get(signal_id)
        if not s:
            return {"success": False, "error": "信号不存在"}
        if s.get("status") != "pending":
            return {"success": False, "error": f"信号状态不是待确认: {s.get('status')}"}
        
        # 检查是否过期
        exp = datetime.fromisoformat(s["expires_at"])
        if datetime.now() > exp:
            s["status"] = "expired"
            return {"success": False, "error": "信号已过期"}
        
        # 执行交易
        strategy_info = {
            "strategy": s.get("strategy"),
            "composite_score": s.get("strategy_score"),
        }
        exec_result = self._execute_trade_with_risk_control(
            str(s.get("action", "buy")),
            s["symbol"],
            s["quantity"],
            strategy_info,
        )
        
        if exec_result.get("success"):
            s["status"] = "executed"
            s["executed_at"] = datetime.now().isoformat()
            s["execution_result"] = exec_result
        else:
            s["status"] = "failed"
            s["error"] = exec_result.get("error")
        
        s["updated_at"] = datetime.now().isoformat()
        self._persist_signals_to_disk()
        
        return {
            "success": exec_result.get("success"),
            "signal": s,
            "error": exec_result.get("error"),
        }

    def mark_signal_result(self, signal_id: str, status: str, extra: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        with self._lock:
            s = self._signals.get(signal_id)
            if not s:
                return None
            s["status"] = status
            s["updated_at"] = datetime.now().isoformat()
            if extra:
                s.update(extra)
            self._persist_signals_to_disk()
            return dict(s)

    def _persist_signals_to_disk(self) -> None:
        """
        将当前内存中的 signals 落盘到磁盘 JSON。
        解决 API 与 Worker 不共享内存导致“待确认列表为空”的问题。
        """
        try:
            with self._lock:
                signals = list(self._signals.values())

            # 控制文件大小：保留最新 N 条
            signals = sorted(
                signals,
                key=lambda x: str(x.get("created_at") or x.get("updated_at") or ""),
                reverse=True,
            )[:AUTO_TRADER_SIGNALS_PERSIST_MAX]

            payload = {"updated_at": datetime.now().isoformat(), "signals": signals}
            tmp = f"{AUTO_TRADER_SIGNALS_PERSIST_FILE}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, AUTO_TRADER_SIGNALS_PERSIST_FILE)
        except Exception:
            pass

    def list_signals(self, status: str = "all") -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._signals.values())
        if status == "all":
            return sorted(rows, key=lambda x: x["created_at"], reverse=True)
        if status == "executed":
            return [r for r in rows if r.get("status") in {"executed", "simulated"}]
        return [r for r in rows if r.get("status") == status]

    def get_signal(self, signal_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._signals.get(signal_id)

    def list_executed_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取已执行的交易记录"""
        with self._lock:
            trades = list(self._executed_trades)
        return sorted(trades, key=lambda x: x.get("executed_at", ""), reverse=True)[:limit]

    def get_status(self, *, for_runtime_export: bool = False) -> dict[str, Any]:
        """获取AutoTrader状态。for_runtime_export 仅 Worker 写 runtime 时用，避免 API 进程误把未运行调度的状态混进 HTTP 顶层。"""
        self._reset_daily_counter_if_needed()
        with self._lock:
            cfg = dict(self._config)
            out: dict[str, Any] = {
                "enabled": cfg.get("enabled", False),
                "auto_execute": cfg.get("auto_execute", True),
                "running": self._running,
                "daily_trade_count": self._daily_trade_count,
                "max_daily_trades": cfg.get("max_daily_trades", 5),
                "daily_loss_circuit_enabled": bool(cfg.get("daily_loss_circuit_enabled", True)),
                "daily_loss_limit_pct": float(cfg.get("daily_loss_limit_pct", 0.03) or 0.03),
                "daily_loss_pct": round(float(self._daily_loss_pct or 0.0), 6),
                "daily_loss_circuit_triggered": bool(self._daily_loss_circuit_triggered),
                "daily_loss_circuit_reason": self._daily_loss_circuit_reason,
                "daily_loss_circuit_at": self._daily_loss_circuit_at,
                "daily_start_equity": self._daily_start_equity,
                "daily_last_equity": self._daily_last_equity,
                "consecutive_loss_stop_enabled": bool(cfg.get("consecutive_loss_stop_enabled", True)),
                "consecutive_loss_stop_count": int(cfg.get("consecutive_loss_stop_count", 3) or 3),
                "consecutive_loss_count": int(self._consecutive_loss_count),
                "consecutive_loss_stop_triggered": bool(self._consecutive_loss_stop_triggered),
                "consecutive_loss_stop_reason": self._consecutive_loss_stop_reason,
                "consecutive_loss_stop_at": self._consecutive_loss_stop_at,
                "last_trade_pnl_estimate": self._last_trade_pnl_estimate,
                "last_scan_at": self._last_scan_at,
                "consecutive_no_signal_rounds": self._consecutive_no_signal_rounds,
                "observer_mode_enabled": bool(cfg.get("observer_mode_enabled", True)),
                "observer_no_signal_rounds": int(cfg.get("observer_no_signal_rounds", 3) or 3),
                "last_observer_push_at": self._last_observer_push_at,
                "total_signals": len(self._signals),
                "pending_signals": len([s for s in self._signals.values() if s.get("status") == "pending"]),
                "executed_signals": len([s for s in self._signals.values() if s.get("status") == "executed"]),
                "research_allocation": {
                    "config_enabled": bool(cfg.get("research_allocation_enabled")),
                    "last_scan": self._last_research_allocation_ctx,
                },
            }
            if for_runtime_export:
                out["scheduler"] = {
                    "scan_in_progress": self._scheduler_scan_in_progress,
                    "scan_started_at": self._scheduler_scan_started_at,
                    "scan_finished_at": self._scheduler_scan_finished_at,
                    "last_error": self._scheduler_last_error,
                }
            return out

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    break
                cfg = dict(self._config)
            if not cfg.get("enabled"):
                time.sleep(5)
                continue
            started_wall = time.perf_counter()
            with self._lock:
                self._scheduler_scan_started_at = datetime.now().isoformat()
                self._scheduler_scan_in_progress = True
            try:
                self.run_scan_once()
            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}"
                with self._lock:
                    self._scheduler_last_error = err_msg[:4000]
                    self._scheduler_scan_finished_at = datetime.now().isoformat()
                logger.exception("auto_trader scheduler run_scan_once failed")
                emit_metric(
                    event="auto_trader.scheduler_scan_once",
                    ok=False,
                    elapsed_ms=(time.perf_counter() - started_wall) * 1000.0,
                    tags={"market": str(cfg.get("market", "us"))},
                    extra={"error": err_msg[:2000]},
                )
            else:
                with self._lock:
                    self._scheduler_last_error = None
                    self._scheduler_scan_finished_at = datetime.now().isoformat()
            finally:
                with self._lock:
                    self._scheduler_scan_in_progress = False
            time.sleep(int(cfg.get("interval_seconds", 300)))

    def start_scheduler(self) -> bool:
        with self._lock:
            if self._running:
                return True
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return True

    def stop_scheduler(self) -> bool:
        with self._lock:
            self._running = False
        return True


# Singleton instance
_auto_trader: Optional[AutoTraderService] = None


def init_auto_trader(
    fetch_bars: Callable[[str, int, str], list[Any]],
    quote_last: Callable[[str], Optional[dict[str, float]]],
    send_feishu: Callable[[str], bool],
    execute_trade: Callable[[str, str, int, float], dict[str, Any]],
    get_positions: Callable[[], dict[str, Any]],
    get_account: Callable[[], dict[str, Any]],
) -> AutoTraderService:
    global _auto_trader
    if _auto_trader is None:
        _auto_trader = AutoTraderService(
            fetch_bars=fetch_bars,
            quote_last=quote_last,
            send_feishu=send_feishu,
            execute_trade=execute_trade,
            get_positions=get_positions,
            get_account=get_account,
        )
    return _auto_trader


def get_auto_trader() -> Optional[AutoTraderService]:
    return _auto_trader
