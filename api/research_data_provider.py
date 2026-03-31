import json
import math
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any, Optional, Protocol


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _http_get_json(url: str, timeout: float = 3.0) -> Optional[dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=max(0.5, float(timeout))) as resp:
            if int(getattr(resp, "status", 200) or 200) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _http_get_any(url: str, timeout: float = 3.0) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=max(0.5, float(timeout))) as resp:
            if int(getattr(resp, "status", 200) or 200) != 200:
                return None
            raw = resp.read().decode("utf-8", errors="ignore")
            return json.loads(raw)
    except Exception:
        return None


def _http_ping(url: str, timeout: float = 3.0) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=max(0.5, float(timeout))) as resp:
            code = int(getattr(resp, "status", 0) or 0)
            return 200 <= code < 500
    except Exception:
        return False


class ResearchProvider(Protocol):
    def get_strong_stocks(self, market: str, top_n: int, kline: str) -> list[dict[str, Any]]:
        ...

    def score_symbol(
        self,
        symbol: str,
        strategies: list[str],
        backtest_days: int,
        kline: str,
        strategy_params_map: Optional[dict[str, dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        ...

    def run_pair_backtest(self, market: str, backtest_days: int, kline: str) -> dict[str, Any]:
        ...


class LongPortResearchProvider:
    def __init__(self, trader: Any) -> None:
        self._trader = trader

    def get_strong_stocks(self, market: str, top_n: int, kline: str) -> list[dict[str, Any]]:
        return self._trader.screen_strong_stocks(market=market, limit=max(1, int(top_n)), kline=str(kline))

    def score_symbol(
        self,
        symbol: str,
        strategies: list[str],
        backtest_days: int,
        kline: str,
        strategy_params_map: Optional[dict[str, dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        return self._trader.score_strategies(
            symbol=symbol,
            strategies=list(strategies),
            days=max(60, min(240, int(backtest_days))),
            kline=str(kline),
            initial_capital=100000.0,
            strategy_params_map=strategy_params_map if isinstance(strategy_params_map, dict) else None,
            cfg=self._trader.get_config(),
        )

    def run_pair_backtest(self, market: str, backtest_days: int, kline: str) -> dict[str, Any]:
        return self._trader.pair_portfolio_backtest(
            market=str(market),
            days=max(90, int(backtest_days)),
            kline=str(kline),
            initial_capital=100000.0,
        )


class OpenBBClient:
    def __init__(self) -> None:
        self.enabled = str(os.getenv("OPENBB_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
        self.base_url = str(os.getenv("OPENBB_BASE_URL", "http://127.0.0.1:6900")).strip().rstrip("/")
        self.timeout = max(1.0, float(os.getenv("OPENBB_TIMEOUT_SECONDS", "3.5")))

    def is_configured(self) -> bool:
        return self.enabled and bool(self.base_url)

    @staticmethod
    def _mean(vals: list[float]) -> float:
        if not vals:
            return 0.0
        return float(sum(vals) / len(vals))

    @staticmethod
    def _std(vals: list[float]) -> float:
        if len(vals) < 2:
            return 0.0
        m = OpenBBClient._mean(vals)
        var = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
        return float(math.sqrt(max(0.0, var)))

    @staticmethod
    def _parse_timestamp(v: Any) -> float:
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v or "").strip()
        if not s:
            return 0.0
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            from datetime import datetime

            return float(datetime.fromisoformat(s).timestamp())
        except Exception:
            return 0.0

    @staticmethod
    def _extract_close_from_row(row: Any) -> Optional[float]:
        if not isinstance(row, dict):
            return None
        for k in ("close", "adj_close", "close_price", "c", "Close"):
            if k in row:
                val = _safe_float(row.get(k), default=float("nan"))
                if math.isfinite(val):
                    return float(val)
        return None

    @staticmethod
    def _extract_rows(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("results", "items", "data", "rows", "historical", "quotes", "values"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
            if isinstance(rows, dict):
                nested = rows.get("data")
                if isinstance(nested, list):
                    return [x for x in nested if isinstance(x, dict)]
        return []

    def _fetch_daily_closes(self, symbol: str, bars: int = 180) -> list[float]:
        sym = urllib.parse.quote_plus(str(symbol or "").strip())
        lim = max(90, min(365, int(bars)))
        end_date = date.today()
        start_date = end_date - timedelta(days=max(220, lim * 3))
        start_q = urllib.parse.quote_plus(start_date.isoformat())
        end_q = urllib.parse.quote_plus(end_date.isoformat())
        candidates = [
            f"{self.base_url}/api/v1/equity/price/historical?symbol={sym}&interval=1d&provider=yfinance&start_date={start_q}&end_date={end_q}",
            f"{self.base_url}/api/v1/equity/price/historical?symbol={sym}&interval=1d&provider=tiingo&start_date={start_q}&end_date={end_q}",
            f"{self.base_url}/api/v1/etf/historical?symbol={sym}&interval=1d&provider=yfinance&start_date={start_q}&end_date={end_q}",
            f"{self.base_url}/api/v1/index/price/historical?symbol={sym}&interval=1d&provider=yfinance&start_date={start_q}&end_date={end_q}",
        ]
        for url in candidates:
            payload = _http_get_any(url, timeout=self.timeout)
            rows = self._extract_rows(payload)
            if not rows:
                continue
            rows = sorted(
                rows,
                key=lambda r: self._parse_timestamp(r.get("date") or r.get("datetime") or r.get("timestamp")),
            )
            closes = [self._extract_close_from_row(r) for r in rows]
            out = [float(x) for x in closes if isinstance(x, (int, float)) and math.isfinite(float(x))]
            if len(out) >= 80:
                return out
        return []

    @staticmethod
    def _normalize_symbol_for_openbb(symbol: str, market: str) -> str:
        s = str(symbol or "").strip().upper()
        if not s:
            return s
        m = str(market or "").lower()
        if m == "us" and s.endswith(".US"):
            return s[:-3]
        return s

    def health(self) -> dict[str, Any]:
        if not self.is_configured():
            return {"enabled": self.enabled, "ok": False, "reason": "openbb_disabled_or_unconfigured"}
        url = f"{self.base_url}/"
        info = _http_get_json(url, timeout=self.timeout)
        ok = _http_ping(url, timeout=self.timeout)
        return {
            "enabled": self.enabled,
            "ok": bool(ok),
            "base_url": self.base_url,
            "service": info if isinstance(info, dict) else None,
        }

    def market_regime(self, market: str) -> dict[str, Any]:
        """
        OpenBB 接口版本众多，这里做 best-effort 聚合：
        - 可达则返回轻量 regime 提示
        - 不可达时返回 fallback，不影响主流程
        """
        m = str(market or "us").lower()
        if not self.is_configured():
            return {"market": m, "source": "openbb", "available": False, "reason": "openbb_disabled"}
        benchmark_map = {"us": "SPY", "hk": "2800.HK", "cn": "510300.SH"}
        benchmark = benchmark_map.get(m, "SPY")
        root_url = f"{self.base_url}/"
        if not _http_ping(root_url, timeout=self.timeout):
            return {"market": m, "source": "openbb", "available": False, "reason": "openbb_unreachable"}
        closes = self._fetch_daily_closes(symbol=benchmark, bars=180)
        if len(closes) < 80:
            return {
                "market": m,
                "source": "openbb",
                "symbol": benchmark,
                "available": False,
                "reason": "insufficient_data",
                "regime": "unknown",
            }
        rets: list[float] = []
        for i in range(1, len(closes)):
            prev = float(closes[i - 1])
            cur = float(closes[i])
            if prev <= 0:
                continue
            rets.append(cur / prev - 1.0)
        if len(rets) < 70:
            return {
                "market": m,
                "source": "openbb",
                "symbol": benchmark,
                "available": False,
                "reason": "insufficient_returns",
                "regime": "unknown",
            }
        ret_20 = float(closes[-1] / closes[-21] - 1.0)
        ma20 = self._mean([float(x) for x in closes[-20:]])
        ma60 = self._mean([float(x) for x in closes[-60:]])
        vol_20 = self._std(rets[-20:]) * math.sqrt(252.0)
        rolling_vol20: list[float] = []
        for i in range(20, len(rets) + 1):
            rolling_vol20.append(self._std(rets[i - 20 : i]) * math.sqrt(252.0))
        baseline = rolling_vol20[-120:] if len(rolling_vol20) > 120 else rolling_vol20
        vol_mu = self._mean(baseline) if baseline else vol_20
        vol_sd = self._std(baseline) if baseline else 0.0
        vol_z = float((vol_20 - vol_mu) / max(vol_sd, 1e-6))
        trend_up = bool(ma20 > ma60)
        if ret_20 > 0.0 and trend_up and vol_z < 1.0:
            regime = "risk_on"
        elif (ret_20 < 0.0 and (not trend_up)) or vol_z > 1.5:
            regime = "risk_off"
        else:
            regime = "neutral"
        trend_score = min(abs(ret_20) / 0.06, 1.0)
        ma_score = 1.0 if trend_up else 0.6
        vol_score = max(0.0, 1.0 - min(abs(vol_z) / 2.0, 1.0))
        if regime == "neutral":
            base = 0.45
            conf = base + 0.35 * trend_score + 0.20 * vol_score
        else:
            conf = 0.25 + 0.45 * trend_score + 0.20 * ma_score + 0.10 * vol_score
        confidence = round(max(0.05, min(conf, 0.99)), 3)
        from datetime import datetime

        return {
            "market": m,
            "source": "openbb",
            "symbol": benchmark,
            "available": True,
            "regime": regime,
            "confidence": confidence,
            "as_of": datetime.now().isoformat(),
            "features": {
                "ret_20": round(ret_20, 6),
                "ma20": round(ma20, 4),
                "ma60": round(ma60, 4),
                "vol_20": round(vol_20, 6),
                "vol_z": round(vol_z, 4),
            },
            "note": "openbb_rule_based_v1",
        }

    def symbol_factor(self, symbol: str, market: str, kline: str) -> dict[str, Any]:
        sym = str(symbol or "").strip().upper()
        m = str(market or "us").lower()
        if not sym:
            return {"symbol": sym, "available": False, "reason": "empty_symbol"}
        if not self.is_configured():
            return {
                "symbol": sym,
                "market": m,
                "source": "openbb",
                "available": False,
                "reason": "openbb_disabled",
                "volatility_30d": None,
                "ret_20": None,
                "ma_gap_20": None,
                "sentiment_score": None,
                "quality_score": None,
                "note": "openbb_factor_unavailable",
            }
        _ = kline
        if not _http_ping(f"{self.base_url}/", timeout=self.timeout):
            return {
                "symbol": sym,
                "market": m,
                "source": "openbb",
                "available": False,
                "reason": "openbb_unreachable",
                "volatility_30d": None,
                "ret_20": None,
                "ma_gap_20": None,
                "sentiment_score": None,
                "quality_score": None,
                "note": "openbb_factor_unavailable",
            }
        openbb_symbol = self._normalize_symbol_for_openbb(sym, m)
        closes = self._fetch_daily_closes(symbol=openbb_symbol, bars=200)
        if len(closes) < 60:
            return {
                "symbol": sym,
                "market": m,
                "source": "openbb",
                "available": False,
                "reason": "insufficient_data",
                "symbol_openbb": openbb_symbol,
                "volatility_30d": None,
                "ret_20": None,
                "ma_gap_20": None,
                "sentiment_score": None,
                "quality_score": None,
                "note": "openbb_factor_unavailable",
            }
        rets: list[float] = []
        for i in range(1, len(closes)):
            prev = float(closes[i - 1])
            cur = float(closes[i])
            if prev <= 0:
                continue
            rets.append(cur / prev - 1.0)
        if len(rets) < 40:
            return {
                "symbol": sym,
                "market": m,
                "source": "openbb",
                "available": False,
                "reason": "insufficient_returns",
                "symbol_openbb": openbb_symbol,
                "volatility_30d": None,
                "ret_20": None,
                "ma_gap_20": None,
                "sentiment_score": None,
                "quality_score": None,
                "note": "openbb_factor_unavailable",
            }
        vol_30 = self._std(rets[-30:]) * math.sqrt(252.0) if len(rets) >= 30 else self._std(rets) * math.sqrt(252.0)
        ret_20 = float(closes[-1] / closes[-21] - 1.0) if len(closes) >= 21 and closes[-21] > 0 else 0.0
        ma20 = self._mean([float(x) for x in closes[-20:]])
        ma60 = self._mean([float(x) for x in closes[-60:]])
        close_last = float(closes[-1])
        ma_gap_20 = (close_last / max(ma20, 1e-6)) - 1.0
        trend_up = ma20 > ma60
        # price-only 因子：先交付稳定可复现指标，不引入外部新闻流依赖。
        sentiment_score = 0.5 + 0.35 * math.tanh(ret_20 / 0.08) + 0.15 * math.tanh(ma_gap_20 / 0.05)
        quality_score = 0.65 * max(0.0, 1.0 - min(vol_30 / 0.60, 1.0)) + 0.35 * (1.0 if trend_up else 0.35)
        sentiment_score = max(0.0, min(sentiment_score, 1.0))
        quality_score = max(0.0, min(quality_score, 1.0))
        return {
            "symbol": sym,
            "market": m,
            "source": "openbb",
            "available": True,
            "symbol_openbb": openbb_symbol,
            "volatility_30d": round(float(vol_30), 6),
            "ret_20": round(float(ret_20), 6),
            "ma_gap_20": round(float(ma_gap_20), 6),
            "trend_up": bool(trend_up),
            "sentiment_score": round(float(sentiment_score), 4),
            "quality_score": round(float(quality_score), 4),
            "note": "openbb_factor_v1_price_based",
        }


class ResearchProviderRouter:
    """
    阶段3：统一研究数据层路由。
    当前主数据源为 LongPort；OpenBB 作为外部增强，不进入下单关键路径。
    """

    def __init__(self, primary: ResearchProvider) -> None:
        self.primary = primary
        self.openbb = OpenBBClient()

    def strong_stocks(self, market: str, top_n: int, kline: str) -> list[dict[str, Any]]:
        return self.primary.get_strong_stocks(market=market, top_n=top_n, kline=kline)

    def score_symbol(
        self,
        symbol: str,
        strategies: list[str],
        backtest_days: int,
        kline: str,
        strategy_params_map: Optional[dict[str, dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        return self.primary.score_symbol(
            symbol=symbol,
            strategies=strategies,
            backtest_days=backtest_days,
            kline=kline,
            strategy_params_map=strategy_params_map if isinstance(strategy_params_map, dict) else None,
        )

    def pair_backtest(self, market: str, backtest_days: int, kline: str) -> dict[str, Any]:
        return self.primary.run_pair_backtest(market=market, backtest_days=backtest_days, kline=kline)

    def external_market_regime(self, market: str) -> dict[str, Any]:
        return self.openbb.market_regime(market=market)

    def external_symbol_factors(self, symbols: list[str], market: str, kline: str, limit: int = 8) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for sym in list(symbols or [])[: max(1, int(limit))]:
            out.append(self.openbb.symbol_factor(symbol=sym, market=market, kline=kline))
        return out

    def provider_status(self) -> dict[str, Any]:
        hb = self.openbb.health()
        return {
            "primary": "longport",
            "openbb_enabled": bool(self.openbb.enabled),
            "openbb_connected": bool(hb.get("ok")),
            "openbb_base_url": self.openbb.base_url if self.openbb.enabled else "",
        }

