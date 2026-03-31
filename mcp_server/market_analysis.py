"""
market_analysis.py - 市场环境分析模块
功能：
  - Fear & Greed Index（市场情绪指数）
  - VIX 波动率指数
  - 宏观指标（国债收益率、美元指数等）
  - 板块轮动分析
  - 财经新闻情绪分析
依赖：requests（HTTP请求）
"""
import os
import sys
import requests
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, asdict
import re
from zoneinfo import ZoneInfo
from config.env_loader import load_project_env

load_project_env(Path(__file__).resolve().parents[1])

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY", "") or os.getenv("NEWS_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

# 请求头：避免被 Yahoo 等站点识别为脚本而返回 429
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

_ET = ZoneInfo("America/New_York")
_QUOTE_TS_SOURCE_TZ = ZoneInfo(os.getenv("QUOTE_TS_SOURCE_TZ", "Asia/Shanghai"))


def _as_et_datetime(raw):
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


def _get_realtime_last(quote_obj) -> float:
    """按美东时段优先取实时价，并做时间戳新鲜度校验。"""
    now_et = datetime.now(timezone.utc).astimezone(_ET)
    session = _session_kind_et(now_et)
    candidates = {
        "盘前": getattr(quote_obj, "pre_market_quote", None),
        "盘后": getattr(quote_obj, "post_market_quote", None),
        "夜盘": getattr(quote_obj, "overnight_quote", None),
        "盘中": quote_obj,
    }
    preferred = {
        "盘前": ["盘前", "盘中", "夜盘", "盘后"],
        "盘中": ["盘中", "盘前", "盘后", "夜盘"],
        "盘后": ["盘后", "盘中", "夜盘", "盘前"],
        "夜盘": ["夜盘", "盘后", "盘中", "盘前"],
    }[session]

    for kind in preferred:
        obj = candidates.get(kind)
        if not obj or not getattr(obj, "last_done", None):
            continue
        if kind == "盘中":
            return float(obj.last_done)
        ts = _extract_quote_ts(obj)
        if _is_fresh_for_session(kind, ts, now_et):
            return float(obj.last_done)

    for kind in preferred:
        obj = candidates.get(kind)
        if obj and getattr(obj, "last_done", None):
            return float(obj.last_done)
    return float(quote_obj.last_done)


def _get_with_retry(url: str, timeout: int = 10, max_retries: int = 2) -> requests.Response:
    """带 User-Agent 和 429 重试的 GET"""
    for attempt in range(max_retries + 1):
        r = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
        if r.status_code != 429 or attempt == max_retries:
            return r
        time.sleep(2)
    return r


# ============================================================
# 数据结构
# ============================================================

@dataclass
class MarketSentiment:
    """市场情绪指数"""
    value: int              # 0-100, 越低越恐慌
    level: str              # "极度恐慌" | "恐慌" | "中性" | "贪婪" | "极度贪婪"
    timestamp: str
    components: dict        # 各组成部分（如动量、波动率等）


@dataclass
class MacroIndicator:
    """宏观指标"""
    name: str
    value: float
    unit: str
    change: Optional[float] = None
    change_pct: Optional[float] = None
    timestamp: str = ""
    interpretation: str = ""  # 对市场的影响解读


# ============================================================
# Fear & Greed Index
# ============================================================

class FearGreedIndex:
    """
    Fear & Greed Index API
    数据来源：CNN Fear & Greed Index
    """
    
    # 备用数据源（最后兜底）
    ALTERNATIVE_API = "https://api.alternative.me/fng/?limit=1"

    @staticmethod
    def _clamp_score(v: float) -> int:
        return int(max(0, min(100, round(v))))

    @staticmethod
    def _score_from_change_pct(chg: float, cap: float = 2.5) -> float:
        cap = max(0.1, float(cap))
        clipped = max(-cap, min(cap, float(chg)))
        return ((clipped + cap) / (2 * cap)) * 100.0

    @staticmethod
    def _level_from_value(value: int) -> str:
        if value <= 20:
            return "极度恐慌"
        if value <= 40:
            return "恐慌"
        if value <= 60:
            return "中性"
        if value <= 80:
            return "贪婪"
        return "极度贪婪"

    @staticmethod
    def _get_stock_proxy_sentiment() -> Optional[MarketSentiment]:
        """优先使用美股风险偏好代理（LongPort 实时报价）构建 0-100 情绪值。"""
        ctx = SectorAnalysis._get_quote_ctx()
        if not ctx:
            return None
        # 风险资产 + 防御资产，构造股市风险偏好分。
        risky = ["SPY.US", "QQQ.US", "IWM.US", "DIA.US", "HYG.US"]
        defensive = ["XLP.US", "XLU.US", "TLT.US", "LQD.US"]
        syms = risky + defensive
        try:
            quotes = ctx.quote(syms)
        except Exception:
            return None
        if not quotes or len(quotes) < 4:
            return None

        changes: Dict[str, float] = {}
        for i, sym in enumerate(syms):
            if i >= len(quotes):
                continue
            q = quotes[i]
            try:
                last = _get_realtime_last(q)
                prev = float(getattr(q, "prev_close", 0.0) or 0.0)
                if prev <= 0:
                    continue
                changes[sym] = (last - prev) / prev * 100.0
            except Exception:
                continue

        if len(changes) < 4:
            return None

        risk_vals = [changes[s] for s in risky if s in changes]
        def_vals = [changes[s] for s in defensive if s in changes]
        if not risk_vals:
            return None

        momentum = sum(risk_vals) / len(risk_vals)
        spread = momentum - (sum(def_vals) / len(def_vals) if def_vals else 0.0)

        momentum_score = FearGreedIndex._score_from_change_pct(momentum, cap=2.0)
        spread_score = FearGreedIndex._score_from_change_pct(spread, cap=2.0)

        # VIX 作为反向分：越高越恐慌。若不可用则给中性分，避免拖垮情绪主流程。
        vix = VIXIndicator.get_vix()
        if float(vix.value or 0) > 0:
            vix_score = max(0.0, min(100.0, 100.0 - ((float(vix.value) - 12.0) / 28.0) * 100.0))
        else:
            vix_score = 50.0

        composite = 0.45 * momentum_score + 0.35 * spread_score + 0.20 * vix_score
        value = FearGreedIndex._clamp_score(composite)
        return MarketSentiment(
            value=value,
            level=FearGreedIndex._level_from_value(value),
            timestamp=datetime.now().isoformat(),
            components={
                "source": "stock_proxy_longport",
                "momentum_pct": round(momentum, 3),
                "risk_defensive_spread_pct": round(spread, 3),
                "momentum_score": round(momentum_score, 2),
                "spread_score": round(spread_score, 2),
                "vix_score": round(vix_score, 2),
                "sample_size": len(changes),
            },
        )
    
    @staticmethod
    def get_sentiment() -> MarketSentiment:
        """
        获取市场情绪指数
        返回 0-100 的指数值
        """
        # 1) 优先：股市风险偏好代理（LongPort 实时）
        stock_proxy = FearGreedIndex._get_stock_proxy_sentiment()
        if stock_proxy:
            return stock_proxy

        # 2) 兜底：alternative.me（加密市场情绪，作为弱相关参考）
        try:
            response = requests.get(
                FearGreedIndex.ALTERNATIVE_API,
                headers=HTTP_HEADERS,
                timeout=8
            )
            response.raise_for_status()
            data = response.json()
            if data and "data" in data and len(data["data"]) > 0:
                item = data["data"][0]
                value = FearGreedIndex._clamp_score(float(item.get("value", 50)))
                return MarketSentiment(
                    value=value,
                    level=FearGreedIndex._level_from_value(value),
                    timestamp=datetime.now().isoformat(),
                    components={
                        "source": "alternative_me_crypto",
                        "classification": item.get("value_classification", ""),
                    },
                )
        except Exception:
            pass

        # 3) 最终回退
        return MarketSentiment(
            value=50,
            level="中性",
            timestamp=datetime.now().isoformat(),
            components={"source": "fallback", "note": "情绪源暂不可用，返回中性值"}
        )


# ============================================================
# VIX 波动率指数
# ============================================================

class VIXIndicator:
    """
    VIX 恐慌指数
    通过 CBOE 官方 CSV 获取
    """
    
    CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
    _CACHE_TTL = 30
    _cache: Optional[MacroIndicator] = None
    _cache_ts: float = 0.0
    _last_good: Optional[MacroIndicator] = None
    _last_good_ts: float = 0.0
    _lock = threading.Lock()

    @staticmethod
    def _interp(v: float) -> str:
        if v < 15:
            return "市场波动率极低，投资者过度自信，警惕突然回调"
        if v < 20:
            return "市场波动率正常，整体情绪稳定"
        if v < 30:
            return "市场波动率升高，投资者开始担忧"
        if v < 40:
            return "市场波动率高，恐慌情绪蔓延，可能有抄底机会"
        return "市场波动率极高，极度恐慌，通常是市场底部信号"

    @staticmethod
    def _build_indicator(current: float, previous: float, source: str) -> MacroIndicator:
        change = current - previous
        change_pct = (change / previous * 100) if previous else 0.0
        return MacroIndicator(
            name="VIX 恐慌指数",
            value=round(float(current), 2),
            unit="",
            change=round(float(change), 2),
            change_pct=round(float(change_pct), 2),
            timestamp=datetime.now().isoformat(),
            interpretation=f"{VIXIndicator._interp(float(current))}（来源: {source}）",
        )

    @staticmethod
    def _parse_fred_rows_from_csv(text: str) -> List[float]:
        out: List[float] = []
        for line in text.strip().split("\n")[1:]:
            parts = line.split(",")
            if len(parts) != 2:
                continue
            raw = str(parts[1]).strip()
            if not raw or raw == ".":
                continue
            try:
                out.append(float(raw))
            except Exception:
                continue
        return out

    @staticmethod
    def _from_cboe() -> MacroIndicator:
        response = requests.get(VIXIndicator.CBOE_URL, headers=HTTP_HEADERS, timeout=5)
        response.raise_for_status()
        lines = response.text.strip().split("\n")
        if len(lines) < 3:
            raise ValueError("CBOE VIX 数据不足")
        last = lines[-1].split(",")
        prev = lines[-2].split(",")
        return VIXIndicator._build_indicator(float(last[4]), float(prev[4]), "cboe_csv")

    @staticmethod
    def _from_fred_vixcls() -> MacroIndicator:
        start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
        if FRED_API_KEY:
            url = (
                "https://api.stlouisfed.org/fred/series/observations"
                f"?series_id=VIXCLS&api_key={FRED_API_KEY}&file_type=json&observation_start={start}"
            )
            response = requests.get(url, headers=HTTP_HEADERS, timeout=5)
            response.raise_for_status()
            rows: List[float] = []
            for obs in response.json().get("observations", []):
                raw = obs.get("value")
                if raw and raw != ".":
                    try:
                        rows.append(float(raw))
                    except Exception:
                        continue
        else:
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS&cosd={start}"
            response = requests.get(url, headers=HTTP_HEADERS, timeout=5)
            response.raise_for_status()
            rows = VIXIndicator._parse_fred_rows_from_csv(response.text)
        if len(rows) < 2:
            raise ValueError("FRED VIXCLS 数据不足")
        return VIXIndicator._build_indicator(rows[-1], rows[-2], "fred_vixcls")
    
    @staticmethod
    def get_vix() -> MacroIndicator:
        """获取 VIX 指数"""
        now = time.time()
        with VIXIndicator._lock:
            if (
                VIXIndicator._cache is not None
                and VIXIndicator._cache_ts > 0
                and (now - VIXIndicator._cache_ts) < VIXIndicator._CACHE_TTL
            ):
                return VIXIndicator._cache

        errors: List[str] = []
        for fetcher in (VIXIndicator._from_cboe, VIXIndicator._from_fred_vixcls):
            try:
                item = fetcher()
                with VIXIndicator._lock:
                    VIXIndicator._cache = item
                    VIXIndicator._cache_ts = now
                    VIXIndicator._last_good = item
                    VIXIndicator._last_good_ts = now
                return item
            except Exception as e:
                errors.append(str(e))

        with VIXIndicator._lock:
            if VIXIndicator._last_good is not None and VIXIndicator._last_good_ts > 0:
                age = int(max(0, now - VIXIndicator._last_good_ts))
                stale = VIXIndicator._last_good
                return MacroIndicator(
                    name=stale.name,
                    value=stale.value,
                    unit=stale.unit,
                    change=stale.change,
                    change_pct=stale.change_pct,
                    timestamp=stale.timestamp,
                    interpretation=f"{stale.interpretation}（使用历史缓存，{age}s前）",
                )

        return MacroIndicator(
            name="VIX 恐慌指数",
            value=0.0,
            unit="",
            change=0.0,
            change_pct=0.0,
            timestamp=datetime.now().isoformat(),
            interpretation=f"数据获取失败（CBOE/FRED）: {' | '.join(errors) if errors else 'unknown'}",
        )


# ============================================================
# 国债收益率
# ============================================================

class TreasuryYield:
    """美国国债收益率（FRED 数据源）"""
    
    @staticmethod
    def get_10y_yield() -> MacroIndicator:
        """获取 10 年期国债收益率"""
        try:
            start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
            if FRED_API_KEY:
                url = f"https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key={FRED_API_KEY}&file_type=json&observation_start={start}"
            else:
                url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd={start}"
            response = requests.get(url, headers=HTTP_HEADERS, timeout=5)
            response.raise_for_status()
            
            rows = []
            if FRED_API_KEY:
                data = response.json()
                for obs in data.get("observations", []):
                    v = obs.get("value")
                    if v and v != ".":
                        rows.append(float(v))
            else:
                for line in response.text.strip().split("\n")[1:]:
                    parts = line.split(",")
                    if len(parts) == 2 and parts[1] and parts[1] != ".":
                        rows.append(float(parts[1]))
            if len(rows) < 2:
                raise ValueError("FRED DGS10 数据不足")
            
            current = rows[-1]
            previous = rows[-2]
            change = current - previous
            change_pct = (change / previous * 100) if previous else 0
            
            if current < 2.0:
                interp = "收益率极低，经济衰退担忧，资金避险"
            elif current < 3.0:
                interp = "收益率偏低，经济增长温和"
            elif current < 4.0:
                interp = "收益率正常，经济稳健增长"
            elif current < 5.0:
                interp = "收益率偏高，通胀压力或加息预期"
            else:
                interp = "收益率高，经济过热或严重通胀"
            
            return MacroIndicator(
                name="10年期国债收益率",
                value=round(current, 2),
                unit="%",
                change=round(change, 2),
                change_pct=round(change_pct, 2),
                timestamp=datetime.now().isoformat(),
                interpretation=interp
            )
        except Exception as e:
            return MacroIndicator(
                name="10年期国债收益率",
                value=0,
                unit="%",
                change=0,
                change_pct=0,
                timestamp=datetime.now().isoformat(),
                interpretation=f"数据获取失败: {str(e)}"
            )


# ============================================================
# 美元指数
# ============================================================

class DollarIndex:
    """美元指数（FRED Trade Weighted Dollar Index）"""
    
    @staticmethod
    def get_dxy() -> MacroIndicator:
        """获取美元指数"""
        try:
            start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
            if FRED_API_KEY:
                url = f"https://api.stlouisfed.org/fred/series/observations?series_id=DTWEXBGS&api_key={FRED_API_KEY}&file_type=json&observation_start={start}"
            else:
                url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXBGS&cosd={start}"
            response = requests.get(url, headers=HTTP_HEADERS, timeout=5)
            response.raise_for_status()
            
            rows = []
            if FRED_API_KEY:
                data = response.json()
                for obs in data.get("observations", []):
                    v = obs.get("value")
                    if v and v != ".":
                        rows.append(float(v))
            else:
                for line in response.text.strip().split("\n")[1:]:
                    parts = line.split(",")
                    if len(parts) == 2 and parts[1] and parts[1] != ".":
                        rows.append(float(parts[1]))
            if len(rows) < 2:
                raise ValueError("FRED DTWEXBGS 数据不足")
            
            current = rows[-1]
            previous = rows[-2]
            change = current - previous
            change_pct = (change / previous * 100) if previous else 0
            
            if current < 90:
                interp = "美元疲软，利好美股和大宗商品"
            elif current < 100:
                interp = "美元正常区间"
            elif current < 110:
                interp = "美元偏强，可能压制美股表现"
            else:
                interp = "美元极强，全球资金回流美国，新兴市场承压"
            
            return MacroIndicator(
                name="美元指数 DXY",
                value=round(current, 2),
                unit="",
                change=round(change, 2),
                change_pct=round(change_pct, 2),
                timestamp=datetime.now().isoformat(),
                interpretation=interp
            )
        except Exception as e:
            return MacroIndicator(
                name="美元指数 DXY",
                value=0,
                unit="",
                change=0,
                change_pct=0,
                timestamp=datetime.now().isoformat(),
                interpretation=f"数据获取失败: {str(e)}"
            )


# ============================================================
# 板块轮动分析
# ============================================================

class SectorAnalysis:
    """板块轮动分析"""
    
    # 主要板块 ETF
    SECTOR_ETFS = {
        "XLK": "科技",
        "XLF": "金融",
        "XLV": "医疗",
        "XLE": "能源",
        "XLI": "工业",
        "XLC": "通信",
        "XLY": "可选消费",
        "XLP": "必需消费",
        "XLB": "材料",
        "XLRE": "房地产",
        "XLU": "公用事业",
    }
    
    _cache: Dict = {}
    _cache_ts: float = 0
    _CACHE_TTL = 20  # 20 seconds，优先保证板块轮动的时效性
    _FAIL_COOLDOWN = 120  # 当外部网络异常时，2 分钟内直接返回最近缓存，避免卡顿
    _last_data_source = "unknown"  # real_time | cache | fallback
    _quote_ctx = None
    _quote_ctx_inited = False
    _quote_ctx_last_error = ""

    @staticmethod
    def _get_quote_ctx():
        """懒加载 LongPort QuoteContext（用于实时板块行情）"""
        if SectorAnalysis._quote_ctx_inited:
            return SectorAnalysis._quote_ctx
        SectorAnalysis._quote_ctx_inited = True
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if root not in sys.path:
                sys.path.insert(0, root)
            lp_app_key = os.getenv("LONGPORT_APP_KEY", "")
            lp_app_secret = os.getenv("LONGPORT_APP_SECRET", "")
            lp_access_token = os.getenv("LONGPORT_ACCESS_TOKEN", "")
            if not lp_app_key:
                try:
                    from config.live_settings import live_settings
                    lp_app_key = live_settings.LONGPORT_APP_KEY
                    lp_app_secret = live_settings.LONGPORT_APP_SECRET
                    lp_access_token = live_settings.LONGPORT_ACCESS_TOKEN
                except Exception:
                    pass
            if not lp_app_key:
                SectorAnalysis._quote_ctx_last_error = "LONGPORT credentials missing"
                return None
            from longport.openapi import Config, QuoteContext
            cfg = Config(
                app_key=lp_app_key,
                app_secret=lp_app_secret,
                access_token=lp_access_token,
                enable_overnight=True,
                enable_print_quote_packages=False,
            )
            SectorAnalysis._quote_ctx = QuoteContext(cfg)
            SectorAnalysis._quote_ctx_last_error = ""
            return SectorAnalysis._quote_ctx
        except Exception as e:
            SectorAnalysis._quote_ctx_last_error = str(e)
            return None

    @staticmethod
    def _get_realtime_sector_performance() -> List[Dict]:
        """优先实时源：通过 LongPort ETF quote 计算涨跌幅"""
        ctx = SectorAnalysis._get_quote_ctx()
        if not ctx:
            return []
        try:
            symbols = [f"{symbol}.US" for symbol in SectorAnalysis.SECTOR_ETFS.keys()]
            quotes = ctx.quote(symbols)
            out: List[Dict] = []
            for idx, symbol in enumerate(SectorAnalysis.SECTOR_ETFS.keys()):
                if idx >= len(quotes):
                    continue
                q = quotes[idx]
                last = _get_realtime_last(q)
                prev = float(q.prev_close)
                if prev <= 0:
                    continue
                out.append({
                    "symbol": symbol,
                    "name": SectorAnalysis.SECTOR_ETFS[symbol],
                    "change_pct": round((last - prev) / prev * 100, 2),
                    "latest_price": round(last, 2),
                })
            out.sort(key=lambda x: x["change_pct"], reverse=True)
            return out
        except Exception:
            return []

    @staticmethod
    def _fallback_sectors() -> List[Dict]:
        """兜底板块列表：保证前端总有可展示项"""
        return [
            {
                "symbol": symbol,
                "name": name,
                "change_pct": 0.0,
                "latest_price": 0.0,
            }
            for symbol, name in SectorAnalysis.SECTOR_ETFS.items()
        ]

    @staticmethod
    def get_sector_performance(days: int = 5) -> List[Dict]:
        """获取各板块近期表现（Stooq 数据源，带 5 分钟缓存）"""
        import logging
        log = logging.getLogger("market_analysis")

        now = time.time()
        cache_key = f"sector_{days}"
        if (now - SectorAnalysis._cache_ts < SectorAnalysis._CACHE_TTL
                and cache_key in SectorAnalysis._cache):
            log.info("板块数据命中缓存")
            SectorAnalysis._last_data_source = "cache"
            return SectorAnalysis._cache[cache_key]

        # 1) 优先走实时行情源（LongPort）
        realtime = SectorAnalysis._get_realtime_sector_performance()
        if realtime:
            SectorAnalysis._cache[cache_key] = realtime
            SectorAnalysis._cache_ts = now
            SectorAnalysis._last_data_source = "real_time"
            log.info("板块数据使用实时行情源: %d/%d", len(realtime), len(SectorAnalysis.SECTOR_ETFS))
            return realtime

        log.warning("实时板块行情不可用，降级使用 Stooq 备用源")

        # 2) 实时源不可用时，Stooq 作为备用源
        results = []
        end = datetime.now()
        start = end - timedelta(days=days + 5)
        d1 = start.strftime("%Y%m%d")
        d2 = end.strftime("%Y%m%d")
        
        fail_count = 0
        for symbol, name in SectorAnalysis.SECTOR_ETFS.items():
            try:
                stooq_sym = f"{symbol.lower()}.us"
                url = f"https://stooq.com/q/d/l/?s={stooq_sym}&d1={d1}&d2={d2}&i=d"
                response = requests.get(url, headers=HTTP_HEADERS, timeout=5)
                log.info("Stooq %s -> %s (%d bytes)", stooq_sym, response.status_code, len(response.text))
                response.raise_for_status()
                
                lines = response.text.strip().split("\n")
                if len(lines) < 2:
                    log.warning("Stooq %s 返回无数据行", stooq_sym)
                    continue
                
                data_rows = lines[1:]
                closes = []
                for row in data_rows:
                    parts = row.split(",")
                    if len(parts) >= 5 and parts[4]:
                        closes.append(float(parts[4]))
                
                tail = closes[-days:] if len(closes) >= days else closes
                if len(tail) >= 2:
                    change_pct = (tail[-1] - tail[0]) / tail[0] * 100
                    results.append({
                        "symbol": symbol,
                        "name": name,
                        "change_pct": round(change_pct, 2),
                        "latest_price": round(tail[-1], 2),
                    })
                fail_count = 0
            except Exception as e:
                log.warning("Stooq %s 失败: %s", symbol, e)
                fail_count += 1
                # 只有连续多次失败才中断，降低误判 fallback 的概率
                if fail_count >= 5:
                    log.warning("Stooq 连续失败，提前结束本轮板块抓取")
                    break
            time.sleep(0.1)
        
        results.sort(key=lambda x: x["change_pct"], reverse=True)

        # 若抓取失败，优先返回历史缓存（即使过期也优于空数据）
        if not results:
            if cache_key in SectorAnalysis._cache and SectorAnalysis._cache[cache_key]:
                log.info("板块抓取失败，使用历史缓存数据")
                SectorAnalysis._last_data_source = "cache"
                return SectorAnalysis._cache[cache_key]
            log.warning("板块抓取失败且无缓存，返回兜底板块列表")
            SectorAnalysis._last_data_source = "fallback"
            return SectorAnalysis._fallback_sectors()

        # 仅在有有效数据时刷新缓存，避免空结果污染缓存
        SectorAnalysis._cache[cache_key] = results
        SectorAnalysis._cache_ts = now
        SectorAnalysis._last_data_source = "fallback"

        log.info("板块数据获取完成: %d/%d", len(results), len(SectorAnalysis.SECTOR_ETFS))
        return results


# ============================================================
# 新闻情绪 + 风险资产温度
# ============================================================

class NewsSentiment:
    """新闻情绪（Finnhub + Tiingo，关键词打分）"""

    POS_WORDS = ["beat", "surge", "rally", "growth", "strong", "optimistic", "upgrade", "bullish"]
    NEG_WORDS = ["miss", "drop", "selloff", "recession", "weak", "downgrade", "bearish", "crisis"]

    @staticmethod
    def _keyword_score(text: str) -> float:
        t = (text or "").lower()
        pos = sum(1 for w in NewsSentiment.POS_WORDS if w in t)
        neg = sum(1 for w in NewsSentiment.NEG_WORDS if w in t)
        total = pos + neg
        if total == 0:
            return 0.0
        return (pos - neg) / total

    @staticmethod
    def _finnhub_score() -> Dict:
        if not FINNHUB_API_KEY:
            return {"score": 0.0, "sample_size": 0, "source": "finnhub", "note": "FINNHUB_API_KEY 未配置"}
        try:
            url = f"https://finnhub.io/api/v1/news?category=general&token={FINNHUB_API_KEY}"
            r = requests.get(url, headers=HTTP_HEADERS, timeout=3)
            r.raise_for_status()
            items = r.json()[:30] if isinstance(r.json(), list) else []
            scores = []
            for it in items:
                text = f"{it.get('headline', '')} {it.get('summary', '')}"
                scores.append(NewsSentiment._keyword_score(text))
            score = round(sum(scores) / len(scores), 3) if scores else 0.0
            return {"score": score, "sample_size": len(scores), "source": "finnhub"}
        except Exception as e:
            return {"score": 0.0, "sample_size": 0, "source": "finnhub", "error": str(e)}

    @staticmethod
    def _tiingo_score() -> Dict:
        if not TIINGO_API_KEY:
            return {"score": 0.0, "sample_size": 0, "source": "tiingo", "note": "TIINGO_API_KEY 未配置"}
        try:
            url = "https://api.tiingo.com/tiingo/news?limit=30&sortBy=publishedDate"
            headers = dict(HTTP_HEADERS)
            headers["Authorization"] = f"Token {TIINGO_API_KEY}"
            r = requests.get(url, headers=headers, timeout=3)
            r.raise_for_status()
            payload = r.json()
            arts = payload if isinstance(payload, list) else []
            scores = []
            for it in arts:
                text = f"{it.get('title', '')} {it.get('description', '')} {it.get('source', '')}"
                scores.append(NewsSentiment._keyword_score(text))
            score = round(sum(scores) / len(scores), 3) if scores else 0.0
            return {"score": score, "sample_size": len(scores), "source": "tiingo"}
        except Exception as e:
            return {"score": 0.0, "sample_size": 0, "source": "tiingo", "error": str(e)}

    @staticmethod
    def get_market_news_sentiment() -> Dict:
        fin = NewsSentiment._finnhub_score()
        tng = NewsSentiment._tiingo_score()
        scores = []
        if fin.get("sample_size", 0) > 0:
            scores.append(fin["score"])
        if tng.get("sample_size", 0) > 0:
            scores.append(tng["score"])
        combined = round(sum(scores) / len(scores), 3) if scores else 0.0
        level = "中性"
        if combined >= 0.2:
            level = "偏乐观"
        elif combined <= -0.2:
            level = "偏悲观"
        return {
            "score": combined,
            "level": level,
            "finnhub": fin,
            "tiingo": tng,
            "timestamp": datetime.now().isoformat(),
        }


class CryptoRisk:
    """风险资产温度（优先 LongPort 实时ETF，降级 CoinGecko）"""

    BTC_ETFS = ["IBIT.US", "FBTC.US", "BITB.US", "ARKB.US"]
    ETH_ETFS = ["ETHA.US", "ETHE.US", "ETHW.US"]
    _cache: Dict = {}
    _cache_ts: float = 0.0

    @staticmethod
    def _get_etf_change(symbols: List[str]) -> Optional[float]:
        """尝试多个 ETF，返回第一个可用的实时涨跌幅（%）"""
        ctx = SectorAnalysis._get_quote_ctx()
        if not ctx:
            return None
        for sym in symbols:
            try:
                q = ctx.quote([sym])
                if not q:
                    continue
                last = _get_realtime_last(q[0])
                prev = float(q[0].prev_close)
                if prev <= 0:
                    continue
                return round((last - prev) / prev * 100, 2)
            except Exception:
                continue
        return None

    @staticmethod
    def _from_longport_etf() -> Optional[Dict]:
        btc = CryptoRisk._get_etf_change(CryptoRisk.BTC_ETFS)
        eth = CryptoRisk._get_etf_change(CryptoRisk.ETH_ETFS)
        if btc is None and eth is None:
            return None
        # 其中一个拿不到时，使用可用值填充，避免整体降级
        if btc is None:
            btc = eth if eth is not None else 0.0
        if eth is None:
            eth = btc if btc is not None else 0.0
        avg = round((btc + eth) / 2, 2)
        level = "中性"
        if avg >= 3:
            level = "风险偏好升温"
        elif avg <= -3:
            level = "风险偏好降温"
        return {
            "btc_change_24h": round(btc, 2),
            "eth_change_24h": round(eth, 2),
            "avg_change_24h": avg,
            "level": level,
            "timestamp": datetime.now().isoformat(),
            "source": "longport_etf",
        }

    @staticmethod
    def get_risk_temperature() -> Dict:
        # 优先使用 LongPort 实时ETF，减少第三方加密数据源波动带来的不稳定
        etf_data = CryptoRisk._from_longport_etf()
        if etf_data:
            CryptoRisk._cache = dict(etf_data)
            CryptoRisk._cache_ts = time.time()
            return etf_data

        fallback_err = {"longport_etf_error": SectorAnalysis._quote_ctx_last_error or "etf quotes unavailable"}
        try:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
            headers = dict(HTTP_HEADERS)
            if COINGECKO_API_KEY:
                headers["x-cg-pro-api-key"] = COINGECKO_API_KEY
            r = requests.get(url, headers=headers, timeout=3)
            r.raise_for_status()
            data = r.json()
            btc = float(data.get("bitcoin", {}).get("usd_24h_change", 0) or 0)
            eth = float(data.get("ethereum", {}).get("usd_24h_change", 0) or 0)
            avg = round((btc + eth) / 2, 2)
            level = "中性"
            if avg >= 3:
                level = "风险偏好升温"
            elif avg <= -3:
                level = "风险偏好降温"
            return {
                "btc_change_24h": round(btc, 2),
                "eth_change_24h": round(eth, 2),
                "avg_change_24h": avg,
                "level": level,
                "timestamp": datetime.now().isoformat(),
                "source": "coingecko",
            }
        except Exception as e:
            fallback_err["coingecko_error"] = str(e)
            if CryptoRisk._cache and CryptoRisk._cache_ts:
                age = int(max(0, time.time() - CryptoRisk._cache_ts))
                cached = dict(CryptoRisk._cache)
                cached["source"] = "cache"
                cached["cache_age_seconds"] = age
                cached["fallback_error"] = fallback_err
                return cached
            return {
                "btc_change_24h": 0.0,
                "eth_change_24h": 0.0,
                "avg_change_24h": 0.0,
                "level": "中性",
                "timestamp": datetime.now().isoformat(),
                "source": "fallback_zero",
                "error": fallback_err,
            }


# ============================================================
# 综合市场分析
# ============================================================

class MarketAnalyzer:
    """综合市场分析器"""
    _CACHE_TTL = 20
    _cache: Dict = {}
    _cache_ts: float = 0.0
    _lock = threading.Lock()

    @staticmethod
    def _fallback_sentiment() -> MarketSentiment:
        return MarketSentiment(
            value=50,
            level="中性",
            timestamp=datetime.now().isoformat(),
            components={"note": "fallback"},
        )

    @staticmethod
    def _fallback_indicator(name: str, unit: str = "") -> MacroIndicator:
        return MacroIndicator(
            name=name,
            value=0.0,
            unit=unit,
            change=0.0,
            change_pct=0.0,
            timestamp=datetime.now().isoformat(),
            interpretation="fallback",
        )
    
    @staticmethod
    def get_comprehensive_analysis() -> Dict:
        """获取综合市场分析"""
        now = time.time()
        with MarketAnalyzer._lock:
            if (
                MarketAnalyzer._cache
                and MarketAnalyzer._cache_ts
                and now - MarketAnalyzer._cache_ts < MarketAnalyzer._CACHE_TTL
            ):
                cached = dict(MarketAnalyzer._cache)
                cached["data_source"] = "cache"
                cached["cache_age_seconds"] = int(now - MarketAnalyzer._cache_ts)
                return cached

        # 外部数据源并发拉取 + 硬超时，避免慢接口拖垮总体响应
        pool = ThreadPoolExecutor(max_workers=6)
        try:
            futures = {
                "sentiment": pool.submit(FearGreedIndex.get_sentiment),
                "vix": pool.submit(VIXIndicator.get_vix),
                "treasury": pool.submit(TreasuryYield.get_10y_yield),
                "dollar": pool.submit(DollarIndex.get_dxy),
                "news": pool.submit(NewsSentiment.get_market_news_sentiment),
                "crypto": pool.submit(CryptoRisk.get_risk_temperature),
            }
            done, _ = wait(list(futures.values()), timeout=3.2)
            done_set = set(done)

            def _pick(name: str, fallback):
                fut = futures[name]
                if fut not in done_set:
                    return fallback
                try:
                    return fut.result()
                except Exception:
                    return fallback

            sentiment = _pick("sentiment", MarketAnalyzer._fallback_sentiment())
            vix = _pick("vix", MarketAnalyzer._fallback_indicator("VIX 恐慌指数"))
            treasury = _pick("treasury", MarketAnalyzer._fallback_indicator("10年期国债收益率", "%"))
            dollar = _pick("dollar", MarketAnalyzer._fallback_indicator("美元指数"))
            news = _pick(
                "news",
                {
                    "score": 0.0,
                    "level": "中性",
                    "summary": "fallback",
                    "sample_count": 0,
                    "source": "fallback",
                },
            )
            crypto = _pick(
                "crypto",
                {
                    "btc_change_24h": 0.0,
                    "eth_change_24h": 0.0,
                    "avg_change_24h": 0.0,
                    "level": "中性",
                    "timestamp": datetime.now().isoformat(),
                    "source": "fallback",
                },
            )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        
        # 综合判断市场环境
        score = 0
        
        # 情绪指数评分
        if sentiment.value < 30:
            score -= 2  # 极度恐慌，看跌
        elif sentiment.value < 50:
            score -= 1  # 偏恐慌
        elif sentiment.value > 70:
            score += 2  # 贪婪，警惕回调
        
        # VIX 评分
        if vix.value > 30:
            score -= 2  # 高波动，恐慌
        elif vix.value < 15:
            score += 1  # 低波动，平稳
        
        # 国债收益率评分（简化）
        if treasury.value > 4.5:
            score -= 1  # 高利率，压制估值
        
        # 新闻情绪评分
        if news["score"] <= -0.2:
            score -= 1
        elif news["score"] >= 0.2:
            score += 1
        
        # 风险资产温度评分（BTC/ETH）
        if crypto["avg_change_24h"] >= 3:
            score += 1
        elif crypto["avg_change_24h"] <= -3:
            score -= 1
        
        # 综合建议
        if score <= -3:
            market_env = "极度恐慌，可能是抄底机会"
            strategy = "防守为主，关注价值股和债券，耐心等待底部信号"
        elif score <= -1:
            market_env = "偏悲观，谨慎观望"
            strategy = "减少仓位，持有现金，等待市场企稳"
        elif score <= 1:
            market_env = "中性平衡"
            strategy = "正常配置，关注个股机会"
        elif score <= 3:
            market_env = "偏乐观，但注意风险"
            strategy = "可适度进攻，但设置止损，警惕回调"
        else:
            market_env = "过度乐观，泡沫风险"
            strategy = "锁定利润，降低仓位，市场可能见顶"
        
        payload = {
            "market_environment": market_env,
            "strategy_recommendation": strategy,
            "score": score,
            "indicators": {
                "fear_greed_index": asdict(sentiment),
                "vix": asdict(vix),
                "treasury_10y": asdict(treasury),
                "dollar_index": asdict(dollar),
                "news_sentiment": news,
                "crypto_risk": crypto,
            },
            "analysis_time": datetime.now().isoformat(),
            "data_source": "real_time",
        }
        with MarketAnalyzer._lock:
            MarketAnalyzer._cache = dict(payload)
            MarketAnalyzer._cache_ts = now
        return payload
    
    @staticmethod
    def get_sector_rotation(days: int = 5) -> Dict:
        """获取板块轮动分析"""
        sectors = SectorAnalysis.get_sector_performance(days=days)
        data_source = SectorAnalysis._last_data_source or "unknown"
        data_source_label = {
            "real_time": "实时",
            "cache": "缓存",
            "fallback": "兜底",
        }.get(data_source, "未知")
        age_seconds = int(max(0, time.time() - SectorAnalysis._cache_ts)) if SectorAnalysis._cache_ts else None
        last_refresh_ts = datetime.fromtimestamp(SectorAnalysis._cache_ts).isoformat() if SectorAnalysis._cache_ts else None

        if not sectors:
            sectors = SectorAnalysis._fallback_sectors()
            data_source = "fallback"
            data_source_label = "兜底"

        # 找出强势和弱势板块
        top3 = sectors[:3]
        bottom3 = sectors[-3:]
        
        # 分析轮动特征
        if top3[0]["name"] in ["科技", "可选消费"]:
            rotation_phase = "成长板块领涨，市场风险偏好高"
        elif top3[0]["name"] in ["必需消费", "公用事业", "医疗"]:
            rotation_phase = "防御板块领涨，市场避险情绪浓"
        elif top3[0]["name"] in ["金融", "工业", "材料"]:
            rotation_phase = "周期板块领涨，经济复苏预期强"
        elif top3[0]["name"] in ["能源"]:
            rotation_phase = "能源板块领涨，通胀或地缘政治担忧"
        else:
            rotation_phase = "板块轮动不明显"
        
        return {
            "rotation_phase": rotation_phase,
            "data_source": data_source,
            "data_source_label": data_source_label,
            "age_seconds": age_seconds,
            "last_refresh_ts": last_refresh_ts,
            "top_performers": top3,
            "bottom_performers": bottom3,
            "all_sectors": sectors,
            "analysis_time": datetime.now().isoformat(),
        }


# ============================================================
# 对外接口
# ============================================================

def get_market_sentiment() -> Dict:
    """获取市场情绪指数"""
    sentiment = FearGreedIndex.get_sentiment()
    return asdict(sentiment)


def get_macro_indicators() -> Dict:
    """获取宏观指标"""
    return {
        "vix": asdict(VIXIndicator.get_vix()),
        "treasury_10y": asdict(TreasuryYield.get_10y_yield()),
        "dollar_index": asdict(DollarIndex.get_dxy()),
    }


def get_comprehensive_analysis() -> Dict:
    """获取综合市场分析"""
    return MarketAnalyzer.get_comprehensive_analysis()


def get_sector_rotation(days: int = 5) -> Dict:
    """获取板块轮动分析"""
    return MarketAnalyzer.get_sector_rotation(days=days)


# ============================================================
# 测试代码
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("市场环境分析测试")
    print("=" * 60)
    
    print("\n1. 市场情绪指数：")
    sentiment = get_market_sentiment()
    print(json.dumps(sentiment, indent=2, ensure_ascii=False))
    
    print("\n2. 宏观指标：")
    macro = get_macro_indicators()
    print(json.dumps(macro, indent=2, ensure_ascii=False))
    
    print("\n3. 综合分析：")
    analysis = get_comprehensive_analysis()
    print(json.dumps(analysis, indent=2, ensure_ascii=False))
    
    print("\n4. 板块轮动：")
    sectors = get_sector_rotation()
    print(json.dumps(sectors, indent=2, ensure_ascii=False))