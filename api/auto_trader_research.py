import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import pstdev
from datetime import datetime
from typing import Any, Callable, Optional

_logger = logging.getLogger(__name__)
from api.research_data_provider import LongPortResearchProvider, ResearchProviderRouter
from mcp_server.backtest_engine import BacktestEngine
from mcp_server.ml_common import FEATURE_COLUMNS, build_ml_feature_frame, create_ml_classifier, walk_forward_probability_map
from mcp_server.strategies import get_strategy, list_strategy_names

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH_SNAPSHOT_FILE = os.path.join(ROOT, ".auto_trader_research.snapshot.json")
RESEARCH_MODEL_REGISTRY_FILE = os.path.join(ROOT, ".auto_trader_research.models.json")
RESEARCH_AB_REPORT_FILE = os.path.join(ROOT, ".auto_trader_research.ab_report.json")
RESEARCH_AB_REPORT_MD_FILE = os.path.join(ROOT, ".auto_trader_research.ab_report.md")
# research/strategy_matrix/ml_matrix 历史快照（每市场每类型最多保留 3 份）
HISTORY_BASE_DIR = os.path.join(ROOT, ".auto_trader_research.history")
HISTORY_KEEP_LATEST_N = 3
# 旧版单文件路径（仅作读取回退；新结果写入按市场分文件）
_RESEARCH_STRATEGY_MATRIX_LEGACY = os.path.join(ROOT, ".auto_trader_research.strategy_matrix.json")
_RESEARCH_ML_MATRIX_LEGACY = os.path.join(ROOT, ".auto_trader_research.ml_matrix.json")


def _history_type_normalize(t: str) -> str:
    x = str(t or "").strip().lower()
    if x in {"research", "rs"}:
        return "research"
    if x in {"strategy_matrix", "sm"}:
        return "strategy_matrix"
    if x in {"ml_matrix", "mm"}:
        return "ml_matrix"
    return x


def _history_market_normalize(market: Optional[str]) -> str:
    return _normalize_research_market(market)


def _history_dir(history_type: str, market: Optional[str]) -> str:
    t = _history_type_normalize(history_type)
    m = _history_market_normalize(market)
    return os.path.join(HISTORY_BASE_DIR, t, m)


def _ts_compact_now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _write_json_default_str(path: str, data: dict[str, Any]) -> None:
    # 用于 history 文件，保证 datetime/Decimal 等可序列化
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
    except Exception:
        pass


def _enforce_history_keep_latest(history_type: str, market: Optional[str]) -> None:
    t = _history_type_normalize(history_type)
    if t not in {"research", "strategy_matrix", "ml_matrix"}:
        return
    d = _history_dir(t, market)
    if not os.path.isdir(d):
        return
    try:
        rows: list[tuple[datetime, str]] = []
        for fn in os.listdir(d):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(d, fn)
            data = _read_json(p)
            if not isinstance(data, dict):
                continue
            sid = str(data.get("snapshot_id") or fn).strip()
            ga = data.get("generated_at") or data.get("meta", {}).get("generated_at")
            if not ga:
                continue
            try:
                dt = datetime.fromisoformat(str(ga))
            except Exception:
                continue
            rows.append((dt, sid))
        if len(rows) <= HISTORY_KEEP_LATEST_N:
            return
        rows.sort(key=lambda x: x[0], reverse=True)
        keep_sids = {sid for _, sid in rows[:HISTORY_KEEP_LATEST_N]}
        for _, sid in rows[HISTORY_KEEP_LATEST_N:]:
            fp = os.path.join(d, f"{sid}.json")
            if not keep_sids or os.path.exists(fp):
                try:
                    if os.path.exists(fp):
                        os.remove(fp)
                except Exception:
                    pass
    except Exception:
        return


def _save_history_snapshot(
    history_type: str,
    market: Optional[str],
    snapshot_id: str,
    meta: dict[str, Any],
    result: dict[str, Any],
) -> str:
    t = _history_type_normalize(history_type)
    if t not in {"research", "strategy_matrix", "ml_matrix"}:
        return ""
    m = _history_market_normalize(market)
    d = _history_dir(t, m)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return ""
    fp = os.path.join(d, f"{snapshot_id}.json")
    payload = {
        "snapshot_id": snapshot_id,
        "type": t,
        "market": m,
        "generated_at": meta.get("generated_at") or datetime.now().isoformat(),
        "meta": meta,
        "result": result,
    }
    _write_json_default_str(fp, payload)
    _enforce_history_keep_latest(t, m)
    return snapshot_id


def _list_history_snapshots(history_type: str, market: Optional[str]) -> dict[str, Any]:
    t = _history_type_normalize(history_type)
    m = _history_market_normalize(market)
    if t not in {"research", "strategy_matrix", "ml_matrix"}:
        return {"ok": False, "error": "invalid_history_type", "type": history_type}
    d = _history_dir(t, m)
    if not os.path.isdir(d):
        return {"ok": True, "type": t, "market": m, "snapshots": []}
    out: list[dict[str, Any]] = []
    for fn in os.listdir(d):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(d, fn)
        data = _read_json(p)
        if not isinstance(data, dict):
            continue
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        snap_id = str(data.get("snapshot_id") or fn.replace(".json", ""))
        ga = data.get("generated_at") or meta.get("generated_at")
        profile_tag = None
        if t == "strategy_matrix":
            items = result.get("items")
            has_items = isinstance(items, list) and len(items) > 0
            if not has_items:
                profile_tag = "none"
            elif isinstance(result.get("best_balanced"), dict):
                profile_tag = "balanced"
            elif isinstance(result.get("best_defensive"), dict):
                profile_tag = "defensive"
            elif isinstance(result.get("best_aggressive"), dict):
                profile_tag = "aggressive"
            else:
                profile_tag = "ranked"
        out.append(
            {
                "snapshot_id": snap_id,
                "type": t,
                "market": m,
                "generated_at": ga,
                # UI 所需的元信息字段从 meta 里读，必要时从外层字段回退
                "kline": meta.get("kline"),
                "top_n": meta.get("top_n"),
                "backtest_days_requested": meta.get("backtest_days_requested"),
                "backtest_days_used": meta.get("backtest_days_used"),
                "signal_bars_days_requested": meta.get("signal_bars_days_requested"),
                "note": meta.get("note"),
                "profile_tag": profile_tag,
            }
        )
    # 按生成时间降序
    def _parse_dt(x: Any) -> float:
        try:
            return datetime.fromisoformat(str(x)).timestamp()
        except Exception:
            return 0.0

    out.sort(key=lambda x: _parse_dt(x.get("generated_at")), reverse=True)
    return {"ok": True, "type": t, "market": m, "snapshots": out}


def _get_history_snapshot_result(history_type: str, market: Optional[str], snapshot_id: str) -> dict[str, Any]:
    t = _history_type_normalize(history_type)
    if t not in {"research", "strategy_matrix", "ml_matrix"}:
        return {}
    m = _history_market_normalize(market)
    d = _history_dir(t, m)
    fp = os.path.join(d, f"{snapshot_id}.json")
    data = _read_json(fp)
    if not isinstance(data, dict):
        return {}
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def list_research_snapshot_history(market: Optional[str], history_type: str) -> dict[str, Any]:
    """供 API 查询 history 列表"""
    return _list_history_snapshots(history_type=history_type, market=market)


def get_research_snapshot_history_result(
    market: Optional[str],
    history_type: str,
    snapshot_id: str,
) -> dict[str, Any]:
    """供 Worker/ML apply 根据 snapshot_id 取到对应 result"""
    return _get_history_snapshot_result(history_type=history_type, market=market, snapshot_id=snapshot_id)


def _normalize_research_market(market: Optional[str]) -> str:
    m = str(market or "us").strip().lower()
    if m in ("us", "hk", "cn"):
        return m
    return "us"


def research_strategy_matrix_path(market: Optional[str]) -> str:
    return os.path.join(ROOT, f".auto_trader_research.strategy_matrix.{_normalize_research_market(market)}.json")


def research_ml_matrix_path(market: Optional[str]) -> str:
    return os.path.join(ROOT, f".auto_trader_research.ml_matrix.{_normalize_research_market(market)}.json")


def research_strategy_eval_cache_path(market: Optional[str]) -> str:
    return os.path.join(ROOT, f".auto_trader_research.strategy_eval_cache.{_normalize_research_market(market)}.json")


def _bar_cache_signature(rows: list[Any]) -> str:
    if not isinstance(rows, list) or not rows:
        return "0"
    try:
        first = rows[0]
        last = rows[-1]
        first_dt = getattr(first, "date", None)
        last_dt = getattr(last, "date", None)
        first_key = first_dt.isoformat() if hasattr(first_dt, "isoformat") else str(first_dt)
        last_key = last_dt.isoformat() if hasattr(last_dt, "isoformat") else str(last_dt)
        return f"{len(rows)}:{first_key}:{last_key}"
    except Exception:
        return str(len(rows))


def _strategy_eval_cache_key(
    market: str,
    symbol: str,
    strategy: str,
    params_sig: str,
    kline: str,
    backtest_days: int,
    commission_bps: float,
    slippage_bps: float,
    bars_sig: str,
) -> str:
    return "|".join(
        [
            str(market).lower(),
            str(symbol).upper(),
            str(strategy),
            str(params_sig or ""),
            str(kline),
            str(int(backtest_days)),
            f"{float(commission_bps):.4f}",
            f"{float(slippage_bps):.4f}",
            str(bars_sig or "0"),
        ]
    )


def _read_json(path: str) -> dict[str, Any]:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: str, data: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
    except Exception as e:
        _logger.warning("research _write_json failed path=%s err=%s", path, e)


def _write_text(path: str, text: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(text or ""))
            f.write("\n")
    except Exception:
        pass


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _clamp_int(v: Any, default: int, lo: int, hi: int) -> int:
    x = _safe_int(v, default)
    return max(lo, min(hi, x))


def _clamp_float(v: Any, default: float, lo: float, hi: float) -> float:
    x = _safe_float(v, default)
    return max(lo, min(hi, x))


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(float(v) for v in values)
    n = len(xs)
    if n == 1:
        return xs[0]
    qq = max(0.0, min(1.0, float(q)))
    pos = (n - 1) * qq
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    w = pos - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def _strategy_param_grid(overrides: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    cfg = overrides if isinstance(overrides, dict) else {}
    kline_choices = cfg.get("kline_choices", ["30m", "1h", "1d"])
    day_choices = cfg.get("backtest_day_choices", [90, 120, 180])
    commission_choices = cfg.get("commission_bps_choices", [2.0, 3.0])
    slippage_choices = cfg.get("slippage_bps_choices", [4.0, 6.0])
    out: list[dict[str, Any]] = []
    for kline in kline_choices:
        for days in day_choices:
            for com in commission_choices:
                for slp in slippage_choices:
                    out.append(
                        {
                            "kline": str(kline),
                            "backtest_days": _clamp_int(days, 120, 60, 365),
                            "commission_bps": _clamp_float(com, 3.0, 0.0, 50.0),
                            "slippage_bps": _clamp_float(slp, 5.0, 0.0, 50.0),
                        }
                    )
    return out


def _strategy_param_signature(params: dict[str, Any]) -> str:
    if not isinstance(params, dict) or not params:
        return ""
    keys = sorted(params.keys())
    return ",".join(f"{k}={params.get(k)}" for k in keys)


def _strategy_internal_param_grid(
    strategy_name: str,
    overrides: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    cfg = overrides if isinstance(overrides, dict) else {}
    choices_cfg = cfg.get("strategy_param_choices", {})
    by_name = choices_cfg.get(strategy_name) if isinstance(choices_cfg, dict) else None

    def _vals(name: str, default: list[Any]) -> list[Any]:
        if isinstance(by_name, dict) and isinstance(by_name.get(name), list) and by_name.get(name):
            return list(by_name.get(name))
        return list(default)

    out: list[dict[str, Any]] = []
    s = str(strategy_name or "").strip()
    if s == "ma_cross":
        fasts = [_clamp_int(x, 5, 2, 200) for x in _vals("fast", [3, 5, 8, 10])]
        slows = [_clamp_int(x, 20, 3, 300) for x in _vals("slow", [15, 20, 30, 60])]
        for f in fasts:
            for sl in slows:
                if f >= sl:
                    continue
                out.append({"fast": int(f), "slow": int(sl)})
    elif s == "rsi":
        periods = [_clamp_int(x, 14, 5, 60) for x in _vals("period", [10, 14, 21])]
        oversolds = [_clamp_float(x, 30.0, 5.0, 45.0) for x in _vals("oversold", [25, 30, 35])]
        overboughts = [_clamp_float(x, 70.0, 55.0, 95.0) for x in _vals("overbought", [65, 70, 75])]
        for p in periods:
            for os in oversolds:
                for ob in overboughts:
                    if os >= ob:
                        continue
                    out.append({"period": int(p), "oversold": round(float(os), 4), "overbought": round(float(ob), 4)})
    elif s == "macd":
        fasts = [_clamp_int(x, 12, 3, 50) for x in _vals("fast", [8, 12, 16])]
        slows = [_clamp_int(x, 26, 6, 120) for x in _vals("slow", [20, 26, 35])]
        signals = [_clamp_int(x, 9, 3, 40) for x in _vals("signal", [6, 9, 12])]
        for f in fasts:
            for sl in slows:
                if f >= sl:
                    continue
                for sg in signals:
                    out.append({"fast": int(f), "slow": int(sl), "signal": int(sg)})
    elif s == "bollinger":
        periods = [_clamp_int(x, 20, 5, 80) for x in _vals("period", [14, 20, 30])]
        stds = [_clamp_float(x, 2.0, 0.5, 4.5) for x in _vals("std_dev", [1.5, 2.0, 2.5])]
        for p in periods:
            for sd in stds:
                out.append({"period": int(p), "std_dev": round(float(sd), 4)})
    elif s == "donchian_breakout":
        entries = [_clamp_int(x, 20, 5, 120) for x in _vals("entry_period", [15, 20, 30])]
        exits = [_clamp_int(x, 10, 3, 80) for x in _vals("exit_period", [8, 10, 15])]
        for ep in entries:
            for xp in exits:
                if xp >= ep:
                    continue
                out.append({"entry_period": int(ep), "exit_period": int(xp)})
    elif s == "supertrend":
        periods = [_clamp_int(x, 10, 5, 50) for x in _vals("period", [7, 10, 14])]
        multipliers = [_clamp_float(x, 3.0, 1.0, 6.0) for x in _vals("multiplier", [2.0, 3.0, 4.0])]
        for p in periods:
            for m in multipliers:
                out.append({"period": int(p), "multiplier": round(float(m), 4)})
    elif s == "adx_ma_filter":
        fasts = [_clamp_int(x, 10, 3, 80) for x in _vals("fast", [8, 10, 14])]
        slows = [_clamp_int(x, 30, 8, 200) for x in _vals("slow", [20, 30, 45])]
        adx_periods = [_clamp_int(x, 14, 5, 60) for x in _vals("adx_period", [10, 14, 20])]
        adx_thresholds = [_clamp_float(x, 20.0, 5.0, 60.0) for x in _vals("adx_threshold", [15, 20, 25])]
        for f in fasts:
            for sl in slows:
                if f >= sl:
                    continue
                for ap in adx_periods:
                    for at in adx_thresholds:
                        out.append(
                            {
                                "fast": int(f),
                                "slow": int(sl),
                                "adx_period": int(ap),
                                "adx_threshold": round(float(at), 4),
                            }
                        )
    elif s == "beiming":
        overlaps = [_clamp_int(x, 5, 2, 40) for x in _vals("overlap", [4, 5, 8])]
        ratios = [_clamp_float(x, 0.05, 0.01, 0.3) for x in _vals("oscillation_ratio", [0.03, 0.05, 0.08])]
        for ov in overlaps:
            for r in ratios:
                out.append({"overlap": int(ov), "oscillation_ratio": round(float(r), 6)})
    if not out:
        return [{}]
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in out:
        sig = _strategy_param_signature(row)
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(row)
    max_variants = _clamp_int(cfg.get("max_variants_per_strategy"), 48, 1, 300)
    return unique[:max_variants]


def _ml_matrix_param_grid(
    cfg: dict[str, Any],
    overrides: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    ov = overrides if isinstance(overrides, dict) else {}
    default_model = str(cfg.get("ml_model_type", "logreg")).strip().lower()
    model_choices = ov.get("model_type_choices", [default_model, "random_forest", "gbdt"])
    threshold_choices = ov.get("ml_threshold_choices", [0.5, 0.53, 0.56, 0.6])
    horizon_choices = ov.get("ml_horizon_days_choices", [3, 5, 8])
    train_ratio_choices = ov.get("ml_train_ratio_choices", [0.65, 0.7, 0.75])
    wf_window_choices = ov.get("ml_walk_forward_windows_choices", [4, 6])
    base_cost = _estimate_label_cost_bps(cfg)
    cost_choices = ov.get("transaction_cost_bps_choices", [base_cost])
    out: list[dict[str, Any]] = []
    for model_type in model_choices:
        mt = str(model_type).strip().lower()
        if mt not in {"logreg", "random_forest", "gbdt"}:
            continue
        for threshold in threshold_choices:
            th = _clamp_float(threshold, 0.55, 0.5, 0.95)
            for horizon_days in horizon_choices:
                hz = _clamp_int(horizon_days, 5, 1, 30)
                for train_ratio in train_ratio_choices:
                    tr = _clamp_float(train_ratio, 0.7, 0.5, 0.9)
                    for wf_windows in wf_window_choices:
                        wf = _clamp_int(wf_windows, 4, 1, 12)
                        for tc_bps in cost_choices:
                            tc = _clamp_float(tc_bps, base_cost, 0.0, 100.0)
                            out.append(
                                {
                                    "model_type": mt,
                                    "ml_threshold": round(float(th), 4),
                                    "ml_horizon_days": int(hz),
                                    "ml_train_ratio": round(float(tr), 4),
                                    "ml_walk_forward_windows": int(wf),
                                    "transaction_cost_bps": round(float(tc), 4),
                                }
                            )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in out:
        key = (
            row.get("model_type"),
            row.get("ml_threshold"),
            row.get("ml_horizon_days"),
            row.get("ml_train_ratio"),
            row.get("ml_walk_forward_windows"),
            row.get("transaction_cost_bps"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _walk_forward_eval_with_threshold(
    df: Any,
    model_type: str,
    train_ratio: float,
    max_windows: int,
    threshold: float,
    min_train_size: int = 80,
    test_window: int = 20,
) -> dict[str, Any]:
    n = len(df)
    if n < max(80, min_train_size + 20):
        return {"enabled": True, "reason": "insufficient_samples", "samples": n}
    ratio = max(0.5, min(float(train_ratio), 0.9))
    split = int(n * ratio)
    split = max(int(min_train_size), min(split, n - 20))
    if split >= n:
        return {"enabled": True, "reason": "invalid_split", "samples": n}
    X = df[FEATURE_COLUMNS].astype(float).values
    y = df["label"].astype(int).values
    th = max(0.5, min(float(threshold), 0.95))
    tw = max(10, min(int(test_window), 60))
    mw = max(1, min(int(max_windows), 12))
    tp = fp = tn = fn = 0
    windows_done = 0
    start = split
    while start < n and windows_done < mw:
        end = min(start + tw, n)
        X_train = X[:start]
        y_train = y[:start]
        X_test = X[start:end]
        y_test = y[start:end]
        if len(X_train) < min_train_size or len(X_test) == 0:
            break
        if len(set(y_train.tolist())) < 2:
            start = end
            continue
        model = create_ml_classifier(model_type)
        model.fit(X_train, y_train)
        probs = model.predict_proba(X_test)[:, 1]
        preds = (probs >= th).astype(int)
        for i, p in enumerate(preds):
            yi = int(y_test[i])
            pi = int(p)
            if pi == 1 and yi == 1:
                tp += 1
            elif pi == 1 and yi == 0:
                fp += 1
            elif pi == 0 and yi == 0:
                tn += 1
            else:
                fn += 1
        windows_done += 1
        start = end
    total = tp + fp + tn + fn
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    accuracy = ((tp + tn) / total) if total > 0 else 0.0
    coverage = (float(total) / float(n)) if n > 0 else 0.0
    return {
        "enabled": True,
        "windows": windows_done,
        "samples": n,
        "oos_samples": total,
        "accuracy": round(float(accuracy), 4),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "coverage": round(float(coverage), 4),
        "threshold": round(float(th), 4),
    }


def _ml_matrix_constraints(settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    cfg = settings if isinstance(settings, dict) else {}
    return {
        "min_oos_samples": _clamp_int(cfg.get("min_oos_samples"), 200, 20, 20000),
        "min_coverage": _clamp_float(cfg.get("min_coverage"), 0.05, 0.0, 1.0),
        "min_precision": _clamp_float(cfg.get("min_precision"), 0.45, 0.0, 1.0),
        "min_accuracy": _clamp_float(cfg.get("min_accuracy"), 0.52, 0.0, 1.0),
    }


def _ml_matrix_ranking_weights(settings: Optional[dict[str, Any]] = None) -> dict[str, float]:
    cfg = settings if isinstance(settings, dict) else {}
    w = {
        "precision": _clamp_float(cfg.get("precision"), 0.35, 0.0, 1.0),
        "accuracy": _clamp_float(cfg.get("accuracy"), 0.25, 0.0, 1.0),
        "recall": _clamp_float(cfg.get("recall"), 0.2, 0.0, 1.0),
        "coverage_stability": _clamp_float(cfg.get("coverage_stability"), 0.2, 0.0, 1.0),
    }
    s = sum(float(v) for v in w.values())
    if s <= 0:
        return {"precision": 0.35, "accuracy": 0.25, "recall": 0.2, "coverage_stability": 0.2}
    return {k: round(float(v) / s, 6) for k, v in w.items()}


def _row_net_return_with_cost(row: dict[str, Any], commission_bps: float, slippage_bps: float) -> float:
    total_return = _safe_float(row.get("total_return_pct"), 0.0)
    trades = max(0.0, _safe_float(row.get("trades"), 0.0))
    est_cost_pct = trades * (float(commission_bps) + float(slippage_bps)) / 100.0
    return float(total_return - est_cost_pct)


def _matrix_rank_candidates(items: list[dict[str, Any]], max_drawdown_limit_pct: float, min_symbols_used: int) -> dict[str, Any]:
    valid: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        used = _safe_int(row.get("symbols_used"), 0)
        avg_dd = _safe_float(row.get("avg_max_drawdown_pct"), 999.0)
        avg_ret = _safe_float(row.get("avg_net_return_pct"), -999.0)
        avg_sharpe = _safe_float(row.get("avg_sharpe_ratio"), -999.0)
        if used < min_symbols_used:
            continue
        if avg_dd > max_drawdown_limit_pct:
            continue
        if avg_ret <= 0:
            continue
        # Hard filter: non-positive risk-adjusted return quality is rejected.
        if avg_sharpe < 0:
            continue
        valid.append(row)
    if not valid:
        return {"items": [], "best_balanced": None, "best_aggressive": None, "best_defensive": None}

    for row in valid:
        avg_ret = _safe_float(row.get("avg_net_return_pct"), 0.0)
        avg_dd = _safe_float(row.get("avg_max_drawdown_pct"), 0.0)
        avg_sharpe = _safe_float(row.get("avg_sharpe_ratio"), 0.0)
        avg_win = _safe_float(row.get("avg_win_rate_pct"), 0.0)
        # Composite for balanced ranking.
        score = 0.45 * avg_ret + 0.25 * avg_sharpe * 10.0 - 0.20 * avg_dd + 0.10 * avg_win
        row["matrix_score"] = round(float(score), 4)

    best_balanced = max(valid, key=lambda x: _safe_float(x.get("matrix_score"), -9999.0))
    best_aggressive = max(
        valid,
        key=lambda x: (
            _safe_float(x.get("avg_net_return_pct"), -9999.0),
            -_safe_float(x.get("avg_max_drawdown_pct"), 9999.0),
        ),
    )
    best_defensive = min(
        valid,
        key=lambda x: (
            _safe_float(x.get("avg_max_drawdown_pct"), 9999.0),
            -_safe_float(x.get("avg_net_return_pct"), -9999.0),
        ),
    )
    valid.sort(key=lambda x: _safe_float(x.get("matrix_score"), -9999.0), reverse=True)
    return {
        "items": valid,
        "best_balanced": best_balanced,
        "best_aggressive": best_aggressive,
        "best_defensive": best_defensive,
    }


def _pair_pool_rows(cfg: dict[str, Any], market: str) -> list[dict[str, str]]:
    pool = cfg.get("pair_pool") if isinstance(cfg, dict) else {}
    m = str(market or "us").lower()
    rows = pool.get(m) if isinstance(pool, dict) else {}
    out: list[dict[str, str]] = []
    if not isinstance(rows, dict):
        return out
    for long_sym, short_sym in rows.items():
        l = str(long_sym or "").strip().upper()
        s = str(short_sym or "").strip().upper()
        if l and s and l != s:
            out.append({"long_symbol": l, "short_symbol": s})
    return out


def _build_allocation_plan(rows: list[dict[str, Any]], max_single_ratio: float = 0.35) -> list[dict[str, Any]]:
    if not rows:
        return []
    ranked = sorted(rows, key=lambda x: _safe_float(x.get("strength_score"), -9999), reverse=True)
    values = [max(0.01, _safe_float(x.get("strength_score"), 0.01)) for x in ranked]
    total = sum(values) or 1.0
    raw_weights = [v / total for v in values]
    capped = [min(max_single_ratio, w) for w in raw_weights]
    capped_total = sum(capped) or 1.0
    normalized = [w / capped_total for w in capped]
    out: list[dict[str, Any]] = []
    for i, row in enumerate(ranked):
        out.append(
            {
                "symbol": str(row.get("symbol", "")),
                "weight": round(float(normalized[i]), 4),
                "strength_score": round(_safe_float(row.get("strength_score")), 2),
                "price_type": str(row.get("price_type", "")),
            }
        )
    return out


def _normalize_regime_name(regime: dict[str, Any]) -> str:
    x = str(regime.get("regime", "")).strip().lower() if isinstance(regime, dict) else ""
    if x in {"risk_on", "risk_off", "neutral"}:
        return x
    return "neutral"


def _safe_confidence(regime: dict[str, Any]) -> float:
    c = _safe_float(regime.get("confidence"), 0.5) if isinstance(regime, dict) else 0.5
    return max(0.0, min(1.0, c))


def _regime_policy(regime_name: str) -> dict[str, float]:
    if regime_name == "risk_on":
        return {"max_single_ratio": 0.35, "target_gross_exposure": 1.00}
    if regime_name == "risk_off":
        return {"max_single_ratio": 0.18, "target_gross_exposure": 0.45}
    return {"max_single_ratio": 0.28, "target_gross_exposure": 0.75}


def _strategy_multiplier(strategy_name: str, regime_name: str) -> float:
    s = str(strategy_name or "").strip().lower()
    is_trend = any(k in s for k in ("trend", "breakout", "momentum"))
    is_mean_reversion = any(k in s for k in ("mean_reversion", "reversion"))
    is_high_vol = any(k in s for k in ("lever", "scalp", "aggressive", "high_beta"))
    if regime_name == "risk_on":
        if is_trend:
            return 1.10
        if is_mean_reversion:
            return 0.95
        return 1.00
    if regime_name == "risk_off":
        if is_trend:
            return 0.90
        if is_mean_reversion:
            return 1.05
        if is_high_vol:
            return 0.85
        return 1.00
    return 1.00


def _apply_regime_to_scored(scored: list[dict[str, Any]], regime_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in scored:
        if not isinstance(row, dict):
            continue
        x = dict(row)
        base = _safe_float(x.get("composite_score"), -9999.0)
        if base <= -9990:
            out.append(x)
            continue
        mul = _strategy_multiplier(str(x.get("strategy", "")), regime_name)
        x["composite_score_raw"] = round(base, 4)
        x["regime_multiplier"] = round(mul, 4)
        x["composite_score"] = round(base * mul, 4)
        out.append(x)
    out.sort(key=lambda z: _safe_float(z.get("composite_score"), -9999.0), reverse=True)
    return out


def _apply_exposure_cap(allocation: list[dict[str, Any]], target_gross_exposure: float, confidence: float) -> tuple[list[dict[str, Any]], float]:
    tge = max(0.0, min(1.0, float(target_gross_exposure)))
    conf = max(0.0, min(1.0, float(confidence)))
    effective = tge * (0.6 + 0.4 * conf)
    out: list[dict[str, Any]] = []
    for row in allocation:
        x = dict(row)
        w = _safe_float(x.get("weight"), 0.0)
        x["weight_raw"] = round(w, 4)
        x["weight"] = round(max(0.0, w * effective), 4)
        out.append(x)
    return out, round(effective, 4)


def _factor_map(symbol_factors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in symbol_factors:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol", "")).strip().upper()
        if not sym:
            continue
        out[sym] = row
    return out


def _factor_multiplier(factor_row: Optional[dict[str, Any]]) -> float:
    if not isinstance(factor_row, dict) or not bool(factor_row.get("available")):
        return 1.0
    sent = _safe_float(factor_row.get("sentiment_score"), 0.5)
    quality = _safe_float(factor_row.get("quality_score"), 0.5)
    vol = _safe_float(factor_row.get("volatility_30d"), float("nan"))
    ret_20 = _safe_float(factor_row.get("ret_20"), float("nan"))
    sent = max(0.0, min(1.0, sent))
    quality = max(0.0, min(1.0, quality))
    mul = (0.90 + 0.20 * sent) * (0.92 + 0.16 * quality)
    if vol == vol:
        if vol > 0.55:
            mul *= 0.93
        elif vol < 0.20:
            mul *= 1.03
    if ret_20 == ret_20:
        if ret_20 > 0.10:
            mul *= 1.02
        elif ret_20 < -0.10:
            mul *= 0.97
    return max(0.82, min(1.18, float(mul)))


def _apply_symbol_factors_to_rankings(
    rankings: list[dict[str, Any]], symbol_factors: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fmap = _factor_map(symbol_factors)
    out: list[dict[str, Any]] = []
    applied = 0
    for row in rankings:
        if not isinstance(row, dict):
            continue
        x = dict(row)
        sym = str(x.get("symbol", "")).strip().upper()
        frow = fmap.get(sym, {})
        fmul = _factor_multiplier(frow)
        top3 = x.get("top3")
        if isinstance(top3, list):
            next_top3: list[dict[str, Any]] = []
            for item in top3:
                if not isinstance(item, dict):
                    continue
                z = dict(item)
                base = _safe_float(z.get("composite_score"), -9999.0)
                if base <= -9990:
                    next_top3.append(z)
                    continue
                z["composite_score_pre_factor"] = round(base, 4)
                z["factor_multiplier"] = round(fmul, 4)
                z["composite_score"] = round(base * fmul, 4)
                next_top3.append(z)
            next_top3.sort(key=lambda t: _safe_float(t.get("composite_score"), -9999.0), reverse=True)
            x["top3"] = next_top3
            x["best_strategy"] = next_top3[0] if next_top3 else {}
        if frow and bool(frow.get("available")):
            applied += 1
        out.append(x)
    return out, {
        "applied": True,
        "available_symbols": applied,
        "total_symbols": len(out),
        "formula": "score_adj = score_regime * factor_multiplier(sentiment, quality, volatility, ret20)",
    }


def _weight_map(allocation: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in allocation:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol", "")).strip().upper()
        if not sym:
            continue
        out[sym] = _safe_float(row.get("weight"), 0.0)
    return out


def _build_factor_ab_report(
    rankings_base: list[dict[str, Any]],
    rankings_factor: list[dict[str, Any]],
    allocation_base: list[dict[str, Any]],
    allocation_factor: list[dict[str, Any]],
) -> dict[str, Any]:
    def _best_score(row: dict[str, Any]) -> float:
        best = row.get("best_strategy")
        if not isinstance(best, dict):
            return -9999.0
        return _safe_float(best.get("composite_score"), -9999.0)

    base_sorted = sorted([x for x in rankings_base if isinstance(x, dict)], key=_best_score, reverse=True)
    factor_sorted = sorted([x for x in rankings_factor if isinstance(x, dict)], key=_best_score, reverse=True)
    base_top = [str(x.get("symbol", "")).strip().upper() for x in base_sorted[:5] if str(x.get("symbol", "")).strip()]
    factor_top = [str(x.get("symbol", "")).strip().upper() for x in factor_sorted[:5] if str(x.get("symbol", "")).strip()]

    base_set = set(base_top)
    factor_set = set(factor_top)
    overlap = sorted(list(base_set & factor_set))
    entered = sorted(list(factor_set - base_set))
    exited = sorted(list(base_set - factor_set))

    base_avg = 0.0
    if base_sorted:
        base_avg = sum(_best_score(x) for x in base_sorted[: max(1, min(5, len(base_sorted)))]) / max(
            1, min(5, len(base_sorted))
        )
    factor_avg = 0.0
    if factor_sorted:
        factor_avg = sum(_best_score(x) for x in factor_sorted[: max(1, min(5, len(factor_sorted)))]) / max(
            1, min(5, len(factor_sorted))
        )

    wb = _weight_map(allocation_base)
    wf = _weight_map(allocation_factor)
    syms = sorted(set(wb.keys()) | set(wf.keys()))
    turnover = sum(abs(_safe_float(wf.get(s), 0.0) - _safe_float(wb.get(s), 0.0)) for s in syms) / 2.0

    rows: list[dict[str, Any]] = []
    by_base = {str(x.get("symbol", "")).strip().upper(): x for x in rankings_base if isinstance(x, dict)}
    by_factor = {str(x.get("symbol", "")).strip().upper(): x for x in rankings_factor if isinstance(x, dict)}
    for s in syms:
        rb = by_base.get(s, {})
        rf = by_factor.get(s, {})
        bb = rb.get("best_strategy") if isinstance(rb, dict) else {}
        bf = rf.get("best_strategy") if isinstance(rf, dict) else {}
        score_base = _safe_float(bb.get("composite_score"), float("nan")) if isinstance(bb, dict) else float("nan")
        score_factor = _safe_float(bf.get("composite_score"), float("nan")) if isinstance(bf, dict) else float("nan")
        factor_mul = _safe_float(bf.get("factor_multiplier"), 1.0) if isinstance(bf, dict) else 1.0
        rows.append(
            {
                "symbol": s,
                "score_baseline": round(score_base, 4) if score_base == score_base else None,
                "score_with_factor": round(score_factor, 4) if score_factor == score_factor else None,
                "score_delta": round((score_factor - score_base), 4)
                if score_base == score_base and score_factor == score_factor
                else None,
                "factor_multiplier": round(factor_mul, 4),
                "weight_baseline": round(_safe_float(wb.get(s), 0.0), 4),
                "weight_with_factor": round(_safe_float(wf.get(s), 0.0), 4),
                "weight_delta": round(_safe_float(wf.get(s), 0.0) - _safe_float(wb.get(s), 0.0), 4),
            }
        )
    rows.sort(key=lambda x: abs(_safe_float(x.get("weight_delta"), 0.0)), reverse=True)

    return {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "top5_baseline": base_top,
            "top5_with_factor": factor_top,
            "overlap_count": len(overlap),
            "overlap_symbols": overlap,
            "entered_symbols": entered,
            "exited_symbols": exited,
            "avg_best_score_baseline": round(float(base_avg), 4),
            "avg_best_score_with_factor": round(float(factor_avg), 4),
            "avg_best_score_delta": round(float(factor_avg - base_avg), 4),
            "allocation_turnover": round(float(turnover), 4),
        },
        "items": rows[:20],
    }


def _ab_report_markdown(report: dict[str, Any]) -> str:
    sm = report.get("summary") if isinstance(report, dict) else {}
    rows = report.get("items") if isinstance(report, dict) else []
    if not isinstance(sm, dict):
        sm = {}
    if not isinstance(rows, list):
        rows = []
    lines: list[str] = []
    lines.append("# AutoTrader 因子 A/B 报告（最小版）")
    lines.append("")
    lines.append(f"- 生成时间：{report.get('generated_at', '-')}")
    lines.append(
        f"- Top5 重合：{sm.get('overlap_count', 0)} | 入选变化：+{len(sm.get('entered_symbols', []) or [])} / -{len(sm.get('exited_symbols', []) or [])}"
    )
    lines.append(
        f"- 平均最佳分变化：{sm.get('avg_best_score_baseline', 0)} -> {sm.get('avg_best_score_with_factor', 0)} (Δ {sm.get('avg_best_score_delta', 0)})"
    )
    lines.append(f"- 分配换手（L1/2）：{sm.get('allocation_turnover', 0)}")
    lines.append("")
    lines.append("## Top5 对比")
    lines.append("")
    lines.append(f"- Baseline: {', '.join(sm.get('top5_baseline', []) or []) or '-'}")
    lines.append(f"- WithFactor: {', '.join(sm.get('top5_with_factor', []) or []) or '-'}")
    lines.append(f"- Entered: {', '.join(sm.get('entered_symbols', []) or []) or '-'}")
    lines.append(f"- Exited: {', '.join(sm.get('exited_symbols', []) or []) or '-'}")
    lines.append("")
    lines.append("## 关键权重变化（Top 10）")
    lines.append("")
    lines.append("| Symbol | Score(B) | Score(F) | ΔScore | Mul | W(B) | W(F) | ΔW |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for x in rows[:10]:
        if not isinstance(x, dict):
            continue
        lines.append(
            f"| {x.get('symbol','-')} | {x.get('score_baseline','-')} | {x.get('score_with_factor','-')} | {x.get('score_delta','-')} | {x.get('factor_multiplier','-')} | {x.get('weight_baseline','-')} | {x.get('weight_with_factor','-')} | {x.get('weight_delta','-')} |"
        )
    return "\n".join(lines)


def _estimate_label_cost_bps(cfg: dict[str, Any]) -> float:
    cost_cfg = cfg.get("cost_model", {}) if isinstance(cfg, dict) else {}
    commission_bps = _safe_float(cost_cfg.get("commission_bps"), 3.0)
    slippage_bps = _safe_float(cost_cfg.get("slippage_bps"), 5.0)
    return max(0.0, min((commission_bps + slippage_bps) * 2.0, 500.0))


def _build_ml_diagnostics(
    trader: Any,
    cfg: dict[str, Any],
    strong_rows: list[dict[str, Any]],
    kline: str,
) -> dict[str, Any]:
    fetch_bars = getattr(trader, "_fetch_bars", None)
    if not callable(fetch_bars):
        return {"enabled": False, "reason": "fetch_bars_unavailable"}
    horizon_days = max(1, min(_safe_int(cfg.get("ml_horizon_days"), 5), 30))
    train_ratio = max(0.5, min(_safe_float(cfg.get("ml_train_ratio"), 0.7), 0.9))
    walk_forward_windows = max(1, min(_safe_int(cfg.get("ml_walk_forward_windows"), 4), 10))
    bars_days = max(300, min(_safe_int(cfg.get("signal_bars_days"), 300), 365))
    transaction_cost_bps = _estimate_label_cost_bps(cfg)
    requested_model = str(cfg.get("ml_model_type", "logreg")).strip().lower()
    model_types = [requested_model, "logreg", "random_forest", "gbdt"]
    model_types = [m for i, m in enumerate(model_types) if m in {"logreg", "random_forest", "gbdt"} and m not in model_types[:i]]

    symbols = []
    for row in strong_rows:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol", "")).strip().upper()
        if sym:
            symbols.append(sym)
    symbols = symbols[:8]
    if not symbols:
        return {"enabled": False, "reason": "no_symbols"}

    frames = []
    bars_total = 0
    used_symbols = 0
    for sym in symbols:
        try:
            bars = fetch_bars(sym, bars_days, kline)
            bars_total += len(bars or [])
            if not bars:
                continue
            df = build_ml_feature_frame(
                bars,
                horizon_days=horizon_days,
                transaction_cost_bps=transaction_cost_bps,
                symbol=sym,
            )
            if df is None or len(df) < 60:
                continue
            frames.append(df)
            used_symbols += 1
        except Exception:
            continue
    if not frames:
        return {
            "enabled": True,
            "reason": "insufficient_samples",
            "settings": {
                "horizon_days": horizon_days,
                "train_ratio": train_ratio,
                "walk_forward_windows": walk_forward_windows,
                "transaction_cost_bps": transaction_cost_bps,
            },
            "dataset": {
                "symbols_requested": len(symbols),
                "symbols_used": 0,
                "bars_total": bars_total,
                "samples": 0,
            },
        }

    import pandas as pd

    data = pd.concat(frames, ignore_index=True)
    samples = len(data)
    y = data["label"].astype(int).tolist()
    pos = int(sum(y))
    neg = max(0, int(samples - pos))
    pos_ratio = (float(pos) / float(samples)) if samples > 0 else 0.0
    net_rets = [float(v) for v in data["net_future_ret"].astype(float).tolist()]

    model_results: list[dict[str, Any]] = []
    for mt in model_types:
        try:
            wf_probs, wf_summary = walk_forward_probability_map(
                df=data,
                model_type=mt,
                train_ratio=train_ratio,
                min_train_size=80,
                test_window=20,
                max_windows=walk_forward_windows,
            )
            latest_prob = None
            if len(set(y)) >= 2:
                model = create_ml_classifier(mt)
                X = data[FEATURE_COLUMNS].astype(float).values
                model.fit(X, data["label"].astype(int).values)
                latest_prob = float(model.predict_proba(X[-1].reshape(1, -1))[0, 1])
            model_results.append(
                {
                    "model_name": mt,
                    "latest_up_probability": round(latest_prob, 4) if latest_prob is not None else None,
                    "walk_forward": wf_summary,
                    "walk_forward_coverage": len(wf_probs),
                    "metric_score": _safe_float((wf_summary or {}).get("accuracy"), 0.0),
                }
            )
        except Exception as e:
            model_results.append(
                {
                    "model_name": mt,
                    "error": str(e),
                    "walk_forward": {"enabled": True, "reason": "evaluation_error"},
                    "walk_forward_coverage": 0,
                    "metric_score": 0.0,
                }
            )

    model_results.sort(key=lambda x: _safe_float(x.get("metric_score"), -1.0), reverse=True)
    return {
        "enabled": True,
        "settings": {
            "requested_model_type": requested_model if requested_model in {"logreg", "random_forest", "gbdt"} else "logreg",
            "horizon_days": horizon_days,
            "train_ratio": train_ratio,
            "walk_forward_windows": walk_forward_windows,
            "transaction_cost_bps": round(transaction_cost_bps, 4),
            "feature_count": len(FEATURE_COLUMNS),
        },
        "dataset": {
            "symbols_requested": len(symbols),
            "symbols_used": used_symbols,
            "bars_total": bars_total,
            "samples": samples,
        },
        "label_distribution": {
            "positive": pos,
            "negative": neg,
            "positive_ratio": round(pos_ratio, 4),
        },
        "net_future_ret_summary": {
            "mean": round(sum(net_rets) / max(1, len(net_rets)), 6),
            "p10": round(_quantile(net_rets, 0.10), 6),
            "p25": round(_quantile(net_rets, 0.25), 6),
            "p50": round(_quantile(net_rets, 0.50), 6),
            "p75": round(_quantile(net_rets, 0.75), 6),
            "p90": round(_quantile(net_rets, 0.90), 6),
        },
        "models": model_results,
    }


def _update_model_registry(strategy_rankings: list[dict[str, Any]], ml_diagnostics: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    registry = _read_json(RESEARCH_MODEL_REGISTRY_FILE)
    history = registry.get("history")
    if not isinstance(history, list):
        history = []
    by_strategy: dict[str, list[float]] = {}
    for row in strategy_rankings:
        symbol = str(row.get("symbol", ""))
        best = row.get("best_strategy")
        if not isinstance(best, dict):
            continue
        name = str(best.get("strategy", "")).strip()
        score = _safe_float(best.get("composite_score"), -9999)
        if not name:
            continue
        by_strategy.setdefault(name, []).append(score)
    for name, values in by_strategy.items():
        history.append(
            {
                "ts": datetime.now().isoformat(),
                "model_type": "rule_strategy",
                "model_name": name,
                "sample_count": len(values),
                "avg_composite_score": round(sum(values) / max(1, len(values)), 4),
            }
        )
    ml_rows = (ml_diagnostics or {}).get("models", []) if isinstance(ml_diagnostics, dict) else []
    if isinstance(ml_rows, list):
        for row in ml_rows:
            if not isinstance(row, dict):
                continue
            model_name = str(row.get("model_name", "")).strip()
            if not model_name:
                continue
            wf = row.get("walk_forward") if isinstance(row.get("walk_forward"), dict) else {}
            history.append(
                {
                    "ts": datetime.now().isoformat(),
                    "model_type": "ml_classifier",
                    "model_name": f"ml_{model_name}",
                    "sample_count": _safe_int((ml_diagnostics or {}).get("dataset", {}).get("samples"), 0)
                    if isinstance((ml_diagnostics or {}).get("dataset"), dict)
                    else 0,
                    "metric_score": round(_safe_float(row.get("metric_score"), 0.0), 4),
                    "wf_accuracy": round(_safe_float((wf or {}).get("accuracy"), 0.0), 4),
                    "wf_precision": round(_safe_float((wf or {}).get("precision"), 0.0), 4),
                    "wf_recall": round(_safe_float((wf or {}).get("recall"), 0.0), 4),
                    "wf_coverage": round(_safe_float((wf or {}).get("coverage"), 0.0), 4),
                }
            )
    history = history[-200:]
    registry["history"] = history
    _write_json(RESEARCH_MODEL_REGISTRY_FILE, registry)
    return registry


def run_research_snapshot(
    trader: Any,
    market: str = "us",
    kline: str = "1d",
    top_n: int = 8,
    backtest_days: int = 180,
    trace_id: str = "",
) -> dict[str, Any]:
    cfg = trader.get_config() if hasattr(trader, "get_config") else {}
    provider = ResearchProviderRouter(LongPortResearchProvider(trader))
    pair_pool_used = _pair_pool_rows(cfg if isinstance(cfg, dict) else {}, market)
    strategies = list(cfg.get("strategies", [])) if isinstance(cfg, dict) else []
    if not strategies and hasattr(trader, "list_strategy_names"):
        try:
            strategies = list(trader.list_strategy_names())
        except Exception:
            strategies = []
    strong = provider.strong_stocks(market=market, top_n=max(1, int(top_n)), kline=kline)
    ml_diagnostics = _build_ml_diagnostics(trader=trader, cfg=cfg if isinstance(cfg, dict) else {}, strong_rows=strong, kline=kline)
    regime = provider.external_market_regime(market=market)
    regime_name = _normalize_regime_name(regime)
    regime_confidence = _safe_confidence(regime)
    policy = _regime_policy(regime_name)
    # UI 需要显示“请求值/实际用于 score_symbol 的 used 值”
    # score_symbol 实际使用的是 clamp 后值（区间 60~240）。
    score_symbol_used_days = max(60, min(240, int(backtest_days)))
    rankings: list[dict[str, Any]] = []
    for row in strong:
        symbol = str(row.get("symbol", ""))
        if not symbol:
            continue
        try:
            scored = provider.score_symbol(
                symbol=symbol,
                strategies=strategies,
                backtest_days=score_symbol_used_days,
                kline=kline,
            )
        except Exception as e:
            scored = [{"strategy": "__error__", "error": str(e), "composite_score": -9999.0}]
        scored_rows = scored if isinstance(scored, list) else []
        adjusted = _apply_regime_to_scored(scored_rows, regime_name)
        best = adjusted[0] if adjusted else {}
        rankings.append(
            {
                "symbol": symbol,
                "best_strategy": best,
                "top3": adjusted[:3],
            }
        )
    pair_backtest: dict[str, Any] = {}
    try:
        pair_backtest = provider.pair_backtest(
            market=market,
            backtest_days=max(90, int(backtest_days)),
            kline=kline,
        )
    except Exception as e:
        pair_backtest = {"error": str(e)}
    rankings_baseline = json.loads(json.dumps(rankings, ensure_ascii=False, default=str))
    symbol_factors = provider.external_symbol_factors(
        symbols=[str(x.get("symbol", "")) for x in strong],
        market=market,
        kline=kline,
        limit=max(1, int(top_n)),
    )
    rankings, factor_gating = _apply_symbol_factors_to_rankings(rankings, symbol_factors)
    price_type_map = {str(x.get("symbol", "")): str(x.get("price_type", "")) for x in strong if isinstance(x, dict)}
    price_type_map_upper = {str(k).strip().upper(): str(v) for k, v in price_type_map.items()}
    baseline_best_score_map = {
        str(x.get("symbol", "")).strip().upper(): _safe_float((x.get("best_strategy") or {}).get("composite_score"), float("nan"))
        for x in rankings_baseline
        if isinstance(x, dict)
    }
    factor_by_symbol = _factor_map(symbol_factors)
    allocation_seed: list[dict[str, Any]] = []
    allocation_seed_baseline: list[dict[str, Any]] = []
    for row in rankings:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol", "")).strip()
        if not sym:
            continue
        best = row.get("best_strategy")
        best_score = _safe_float(best.get("composite_score"), float("nan")) if isinstance(best, dict) else float("nan")
        fallback_strength = _safe_float(next((x.get("strength_score") for x in strong if str(x.get("symbol", "")) == sym), 0.01), 0.01)
        strength = best_score if best_score == best_score else fallback_strength
        allocation_seed.append(
            {
                "symbol": sym,
                "strength_score": strength,
                "price_type": price_type_map.get(sym, ""),
            }
        )
        sym_upper = sym.upper()
        baseline_score = baseline_best_score_map.get(sym_upper, float("nan"))
        baseline_strength = baseline_score if baseline_score == baseline_score else fallback_strength
        allocation_seed_baseline.append(
            {
                "symbol": sym,
                "strength_score": baseline_strength,
                "price_type": price_type_map_upper.get(sym_upper, ""),
            }
        )
    allocation_baseline = _build_allocation_plan(
        allocation_seed_baseline, max_single_ratio=_safe_float(policy.get("max_single_ratio"), 0.28)
    )
    allocation = _build_allocation_plan(allocation_seed, max_single_ratio=_safe_float(policy.get("max_single_ratio"), 0.28))
    allocation_baseline, _ = _apply_exposure_cap(
        allocation_baseline,
        target_gross_exposure=_safe_float(policy.get("target_gross_exposure"), 0.75),
        confidence=regime_confidence,
    )
    allocation, effective_exposure = _apply_exposure_cap(
        allocation,
        target_gross_exposure=_safe_float(policy.get("target_gross_exposure"), 0.75),
        confidence=regime_confidence,
    )
    factor_ab_report = _build_factor_ab_report(
        rankings_base=rankings_baseline,
        rankings_factor=rankings,
        allocation_base=allocation_baseline,
        allocation_factor=allocation,
    )
    factor_ab_md = _ab_report_markdown(factor_ab_report)
    snapshot = {
        "version": datetime.now().isoformat(),
        "generated_at": datetime.now().isoformat(),
        "trace_id": str(trace_id or ""),
        "market": market,
        "kline": kline,
        "top_n": max(1, int(top_n)),
        "pair_pool_used": pair_pool_used,
        "pair_pool_size": len(pair_pool_used),
        "data_providers": provider.provider_status(),
        "external_research": {
            "market_regime": regime,
            "symbol_factors": symbol_factors,
        },
        "strong_stocks": strong,
        "allocation_plan": allocation,
        "strategy_rankings": rankings,
        "regime_gating": {
            "applied": True,
            "regime_name": regime_name,
            "regime_confidence": round(regime_confidence, 4),
            "max_single_ratio": round(_safe_float(policy.get("max_single_ratio"), 0.28), 4),
            "target_gross_exposure": round(_safe_float(policy.get("target_gross_exposure"), 0.75), 4),
            "effective_exposure": effective_exposure,
            "formula": "effective_exposure = target_gross_exposure * (0.6 + 0.4 * confidence)",
        },
        "factor_gating": factor_gating,
        "factor_ab_report": factor_ab_report,
        "ml_diagnostics": ml_diagnostics,
        "pair_backtest": pair_backtest,
    }
    _write_json(RESEARCH_SNAPSHOT_FILE, snapshot)
    _write_json(RESEARCH_AB_REPORT_FILE, factor_ab_report)
    _write_text(RESEARCH_AB_REPORT_MD_FILE, factor_ab_md)
    _update_model_registry(rankings, ml_diagnostics=ml_diagnostics)
    # 写入 history 快照（每市场每类型最多保留 3 份）
    try:
        market_n = _history_market_normalize(market)
        top_n_req = max(1, int(top_n))
        bt_req = int(backtest_days)
        sid = f"rs-{market_n}-k{str(kline or '1d').strip().lower()}-bt{bt_req}-use{score_symbol_used_days}-top{top_n_req}-{_ts_compact_now()}"
        _save_history_snapshot(
            history_type="research",
            market=market_n,
            snapshot_id=sid,
            meta={
                "kline": str(kline or "1d"),
                "top_n": top_n_req,
                "backtest_days_requested": bt_req,
                "backtest_days_used": score_symbol_used_days,
                "trace_id": str(trace_id or ""),
                "note": "research_snapshot_from_score_symbol_used",
            },
            result=snapshot,
        )
    except Exception:
        pass
    return snapshot


def run_strategy_param_matrix(
    trader: Any,
    market: str = "us",
    top_n: int = 8,
    max_strategies: int = 8,
    max_drawdown_limit_pct: float = 30.0,
    min_symbols_used: int = 4,
    trace_id: str = "",
    matrix_overrides: Optional[dict[str, Any]] = None,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> dict[str, Any]:
    cfg = trader.get_config() if hasattr(trader, "get_config") else {}
    mk = _normalize_research_market(market)
    sm_path = research_strategy_matrix_path(mk)
    provider = ResearchProviderRouter(LongPortResearchProvider(trader))
    ov_early = matrix_overrides if isinstance(matrix_overrides, dict) else {}
    # 默认：使用策略注册表中的全部策略（随 STRATEGY_REGISTRY 增减自动包含）。
    # matrix_overrides.use_config_strategies_only=True 时，仅用自动交易配置里勾选的策略，并受 max_strategies 截断。
    if bool(ov_early.get("use_config_strategies_only")):
        strategy_pool = list(cfg.get("strategies", [])) if isinstance(cfg, dict) else []
        if not strategy_pool and hasattr(trader, "list_strategy_names"):
            try:
                strategy_pool = list(trader.list_strategy_names())
            except Exception:
                strategy_pool = []
        strategy_pool = [str(x).strip() for x in strategy_pool if str(x).strip()][: max(1, int(max_strategies))]
    else:
        try:
            strategy_pool = [str(x).strip() for x in list_strategy_names() if str(x).strip()]
        except Exception:
            strategy_pool = []
        if not strategy_pool and hasattr(trader, "list_strategy_names"):
            try:
                strategy_pool = [str(x).strip() for x in trader.list_strategy_names() if str(x).strip()]
            except Exception:
                strategy_pool = []
    if not strategy_pool:
        row = {
            "generated_at": datetime.now().isoformat(),
            "trace_id": str(trace_id or ""),
            "market": str(market or "us"),
            "ok": False,
            "error": "no_strategy_available",
            "items": [],
        }
        _write_json(sm_path, row)
        return row

    strong = provider.strong_stocks(market=str(market or "us"), top_n=max(1, int(top_n)), kline="1d")
    symbols = [str(x.get("symbol", "")).strip().upper() for x in strong if isinstance(x, dict)]
    symbols = [s for s in symbols if s]
    if not symbols:
        row = {
            "generated_at": datetime.now().isoformat(),
            "trace_id": str(trace_id or ""),
            "market": str(market or "us"),
            "ok": False,
            "error": "no_symbol_available",
            "items": [],
        }
        _write_json(sm_path, row)
        return row

    fixed_kline = str((cfg.get("kline") if isinstance(cfg, dict) else None) or "1d")
    fixed_days = _clamp_int((cfg.get("backtest_days") if isinstance(cfg, dict) else None), 180, 60, 365)
    cm = (cfg.get("cost_model") if isinstance(cfg, dict) else None) or {}
    fixed_com_bps = _clamp_float((cm.get("commission_bps") if isinstance(cm, dict) else None), 3.0, 0.0, 50.0)
    fixed_slp_bps = _clamp_float((cm.get("slippage_bps") if isinstance(cm, dict) else None), 5.0, 0.0, 50.0)
    ov = matrix_overrides if isinstance(matrix_overrides, dict) else {}
    # UI 展示 requested backtest_days：优先使用 matrix_overrides 里的请求值；否则回退到 cfg 的请求值。
    bt_days_requested = _safe_int(
        ov.get("backtest_days"),
        _safe_int((cfg.get("backtest_days") if isinstance(cfg, dict) else None), fixed_days),
    )
    fixed_kline = str(ov.get("kline", fixed_kline) or fixed_kline)
    fixed_days = _clamp_int(ov.get("backtest_days"), fixed_days, 60, 365)
    fixed_com_bps = _clamp_float(ov.get("commission_bps"), fixed_com_bps, 0.0, 50.0)
    fixed_slp_bps = _clamp_float(ov.get("slippage_bps"), fixed_slp_bps, 0.0, 50.0)
    strategy_variants: list[dict[str, Any]] = []
    for strategy_name in strategy_pool:
        for params in _strategy_internal_param_grid(strategy_name=strategy_name, overrides=ov):
            strategy_variants.append(
                {
                    "strategy": str(strategy_name),
                    "params": dict(params) if isinstance(params, dict) else {},
                    "params_sig": _strategy_param_signature(params if isinstance(params, dict) else {}),
                }
            )
    max_total_variants = _clamp_int(ov.get("max_total_variants"), 960, 1, 1200)
    strategy_variants = strategy_variants[:max_total_variants]

    max_cache_entries = _clamp_int(ov.get("max_eval_cache_entries"), 50000, 1000, 200000)
    default_workers = max(1, min(8, int(os.cpu_count() or 4)))
    parallel_workers = _clamp_int(ov.get("parallel_workers"), default_workers, 1, 16)
    chunk_size = max(1, parallel_workers)
    candidates: list[dict[str, Any]] = []
    perf = {"cache_hits": 0, "cache_misses": 0, "early_stop_variants": 0}

    eval_cache_path = research_strategy_eval_cache_path(mk)
    eval_cache_doc = _read_json(eval_cache_path)
    raw_cache_items = eval_cache_doc.get("items") if isinstance(eval_cache_doc, dict) else None
    eval_cache: dict[str, dict[str, Any]] = {}
    if isinstance(raw_cache_items, dict):
        for k, v in raw_cache_items.items():
            if isinstance(v, dict):
                eval_cache[str(k)] = v
    cache_lock = threading.Lock()
    cache_dirty = False

    fetch_bars = getattr(trader, "_fetch_bars", None)
    bars_cache: dict[str, list[Any]] = {}
    bars_sig_cache: dict[str, str] = {}
    if callable(fetch_bars):
        for sym in symbols:
            try:
                rows = fetch_bars(sym, fixed_days, fixed_kline)
            except Exception:
                rows = []
            norm_sym = str(sym).upper()
            rows_list = rows if isinstance(rows, list) else []
            bars_cache[norm_sym] = rows_list
            bars_sig_cache[norm_sym] = _bar_cache_signature(rows_list)

    executor: ThreadPoolExecutor | None = None
    if parallel_workers > 1:
        executor = ThreadPoolExecutor(max_workers=parallel_workers)

    try:
        for variant in strategy_variants:
            strategy_name = str(variant.get("strategy", "")).strip()
            strategy_params = variant.get("params")
            strategy_params = dict(strategy_params) if isinstance(strategy_params, dict) else {}
            params_sig = str(variant.get("params_sig", "") or "")
            try:
                sfn = get_strategy(strategy_name, strategy_params)
                strategy_label = str(getattr(sfn, "__name__", "") or strategy_name)
            except Exception:
                continue
            if callable(cancel_checker) and bool(cancel_checker()):
                out = {
                    "generated_at": datetime.now().isoformat(),
                    "trace_id": str(trace_id or ""),
                    "market": str(market or "us"),
                    "ok": False,
                    "cancelled": True,
                    "candidate_count": len(candidates),
                    "items": candidates[:100],
                }
                _write_json(sm_path, out)
                return out

            per_symbol: list[dict[str, Any]] = []
            sum_ret = 0.0
            sum_dd = 0.0
            sum_sharpe = 0.0
            sum_win = 0.0
            sum_trades = 0.0

            def _eval_one_symbol(sym: str) -> dict[str, Any] | None:
                nonlocal cache_dirty
                bars = bars_cache.get(str(sym).upper()) if bars_cache else None
                if not bars:
                    return None
                cache_key = _strategy_eval_cache_key(
                    market=str(market or "us"),
                    symbol=str(sym),
                    strategy=str(strategy_name),
                    params_sig=params_sig,
                    kline=str(fixed_kline),
                    backtest_days=int(fixed_days),
                    commission_bps=float(fixed_com_bps),
                    slippage_bps=float(fixed_slp_bps),
                    bars_sig=bars_sig_cache.get(str(sym).upper(), "0"),
                )
                with cache_lock:
                    cached = eval_cache.get(cache_key)
                if isinstance(cached, dict):
                    perf["cache_hits"] += 1
                    return {
                        "symbol": str(sym),
                        "net_return_pct": _safe_float(cached.get("net_return_pct"), 0.0),
                        "max_drawdown_pct": _safe_float(cached.get("max_drawdown_pct"), 0.0),
                        "sharpe_ratio": _safe_float(cached.get("sharpe_ratio"), 0.0),
                        "win_rate_pct": _safe_float(cached.get("win_rate_pct"), 0.0),
                        "trades": _safe_int(cached.get("trades"), 0),
                    }
                perf["cache_misses"] += 1
                try:
                    engine = BacktestEngine(
                        bars=bars,
                        symbol=sym,
                        strategy_name=strategy_label,
                        strategy_fn=sfn,
                        initial_capital=100000.0,
                    )
                    r = engine.run()
                except Exception:
                    return None
                row = {
                    "total_return_pct": round(float(getattr(r, "total_return_pct", 0.0)), 4),
                    "max_drawdown_pct": round(float(getattr(r, "max_drawdown_pct", 0.0)), 4),
                    "sharpe_ratio": round(float(getattr(r, "sharpe_ratio", 0.0)), 4),
                    "win_rate_pct": round(float(getattr(r, "win_rate_pct", 0.0)), 4),
                    "trades": int(getattr(r, "total_trades", 0)),
                }
                net_ret = _row_net_return_with_cost(row, commission_bps=fixed_com_bps, slippage_bps=fixed_slp_bps)
                result = {
                    "symbol": str(sym),
                    "net_return_pct": net_ret,
                    "max_drawdown_pct": _safe_float(row.get("max_drawdown_pct"), 0.0),
                    "sharpe_ratio": _safe_float(row.get("sharpe_ratio"), 0.0),
                    "win_rate_pct": _safe_float(row.get("win_rate_pct"), 0.0),
                    "trades": _safe_int(row.get("trades"), 0),
                }
                with cache_lock:
                    eval_cache[cache_key] = {
                        "net_return_pct": round(float(result["net_return_pct"]), 6),
                        "max_drawdown_pct": round(float(result["max_drawdown_pct"]), 6),
                        "sharpe_ratio": round(float(result["sharpe_ratio"]), 6),
                        "win_rate_pct": round(float(result["win_rate_pct"]), 6),
                        "trades": int(result["trades"]),
                        "updated_at": datetime.now().isoformat(),
                    }
                    cache_dirty = True
                return result

            for idx in range(0, len(symbols), chunk_size):
                if callable(cancel_checker) and bool(cancel_checker()):
                    out = {
                        "generated_at": datetime.now().isoformat(),
                        "trace_id": str(trace_id or ""),
                        "market": str(market or "us"),
                        "ok": False,
                        "cancelled": True,
                        "candidate_count": len(candidates),
                        "items": candidates[:100],
                    }
                    _write_json(sm_path, out)
                    return out

                remaining = len(symbols) - idx
                used = len(per_symbol)
                # 早停剪枝 1：剩余 symbol 即使全有效也达不到最小样本要求。
                if used + remaining < max(1, int(min_symbols_used)):
                    perf["early_stop_variants"] += 1
                    break
                # 早停剪枝 2：在最乐观假设下（剩余 symbol 回撤=0）依旧无法满足回撤限制。
                if used > 0:
                    best_case_avg_dd = sum_dd / float(used + remaining)
                    if best_case_avg_dd > float(max_drawdown_limit_pct):
                        perf["early_stop_variants"] += 1
                        break

                chunk_symbols = symbols[idx : idx + chunk_size]
                chunk_rows: list[dict[str, Any]] = []
                if executor and len(chunk_symbols) > 1:
                    futs = [executor.submit(_eval_one_symbol, sym) for sym in chunk_symbols]
                    for fut in as_completed(futs):
                        try:
                            row = fut.result()
                        except Exception:
                            row = None
                        if isinstance(row, dict):
                            chunk_rows.append(row)
                else:
                    for sym in chunk_symbols:
                        row = _eval_one_symbol(sym)
                        if isinstance(row, dict):
                            chunk_rows.append(row)

                for x in chunk_rows:
                    per_symbol.append(x)
                    sum_ret += _safe_float(x.get("net_return_pct"), 0.0)
                    sum_dd += _safe_float(x.get("max_drawdown_pct"), 0.0)
                    sum_sharpe += _safe_float(x.get("sharpe_ratio"), 0.0)
                    sum_win += _safe_float(x.get("win_rate_pct"), 0.0)
                    sum_trades += float(_safe_int(x.get("trades"), 0))

            if not per_symbol:
                continue
            n = len(per_symbol)
            avg_ret = sum_ret / n
            avg_dd = sum_dd / n
            avg_sharpe = sum_sharpe / n
            avg_win = sum_win / n
            avg_trades = sum_trades / n
            top_symbols = sorted(
                per_symbol,
                key=lambda x: (
                    _safe_float(x.get("net_return_pct"), -9999.0),
                    _safe_float(x.get("sharpe_ratio"), -9999.0),
                ),
                reverse=True,
            )[:5]
            candidates.append(
                {
                    "strategy": strategy_name,
                    "strategy_label": strategy_label or strategy_name,
                    "strategy_params": strategy_params,
                    "kline": fixed_kline,
                    "backtest_days": fixed_days,
                    "commission_bps": fixed_com_bps,
                    "slippage_bps": fixed_slp_bps,
                    "symbols_used": n,
                    "symbols_total": len(symbols),
                    "avg_net_return_pct": round(float(avg_ret), 4),
                    "avg_max_drawdown_pct": round(float(avg_dd), 4),
                    "avg_sharpe_ratio": round(float(avg_sharpe), 4),
                    "avg_win_rate_pct": round(float(avg_win), 4),
                    "avg_trades": round(float(avg_trades), 2),
                    "top_symbols": top_symbols,
                }
            )
    finally:
        if executor:
            executor.shutdown(wait=True, cancel_futures=False)
        if cache_dirty:
            if len(eval_cache) > max_cache_entries:
                drop_n = len(eval_cache) - max_cache_entries
                for k in list(eval_cache.keys())[:drop_n]:
                    eval_cache.pop(k, None)
            _write_json(
                eval_cache_path,
                {
                    "updated_at": datetime.now().isoformat(),
                    "market": mk,
                    "items": eval_cache,
                },
            )

    ranked = _matrix_rank_candidates(
        items=candidates,
        max_drawdown_limit_pct=float(max_drawdown_limit_pct),
        min_symbols_used=max(1, int(min_symbols_used)),
    )
    out = {
        "generated_at": datetime.now().isoformat(),
        "trace_id": str(trace_id or ""),
        "market": str(market or "us"),
        "ok": True,
        "grid_size": len(strategy_variants),
        "strategy_count": len(strategy_pool),
        "candidate_count": len(candidates),
        "symbols": symbols,
        "matrix_mode": "strategy_internal_params_only",
        "perf": {
            "parallel_workers": int(parallel_workers),
            "cache_hits": int(perf["cache_hits"]),
            "cache_misses": int(perf["cache_misses"]),
            "cache_entries": len(eval_cache),
            "early_stop_variants": int(perf["early_stop_variants"]),
        },
        "filters": {
            "max_drawdown_limit_pct": float(max_drawdown_limit_pct),
            "min_symbols_used": max(1, int(min_symbols_used)),
        },
        "best_balanced": ranked.get("best_balanced"),
        "best_aggressive": ranked.get("best_aggressive"),
        "best_defensive": ranked.get("best_defensive"),
        "items": (ranked.get("items") or [])[:100],
    }
    _write_json(sm_path, out)
    # 写入 strategy_matrix history 快照（每市场每类型最多保留 3 份）
    try:
        mk_n = _history_market_normalize(mk)
        # 你的要求：strategy_matrix 只展示 requested backtest_days，因此 history metadata 中仅记录 requested。
        sid = (
            f"sm-{mk_n}-k{str((fixed_kline or '1d')).strip().lower()}-bt{int(bt_days_requested)}"
            f"-top{max(1, int(top_n))}-maxS{max(1, int(max_strategies))}"
            f"-dd{int(max(1.0, float(max_drawdown_limit_pct)))}-minSym{max(1, int(min_symbols_used))}"
            f"-{_ts_compact_now()}"
        )
        _save_history_snapshot(
            history_type="strategy_matrix",
            market=mk_n,
            snapshot_id=sid,
            meta={
                "kline": str(fixed_kline or "1d"),
                "top_n": max(1, int(top_n)),
                "backtest_days_requested": int(bt_days_requested),
                "max_strategies": max(1, int(max_strategies)),
                "max_drawdown_limit_pct": float(max_drawdown_limit_pct),
                "min_symbols_used": max(1, int(min_symbols_used)),
                "trace_id": str(trace_id or ""),
                "note": "strategy_matrix_snapshot_from_requested_backtest_days",
            },
            result=out,
        )
    except Exception:
        pass
    return out


def run_ml_param_matrix(
    trader: Any,
    market: str = "us",
    kline: str = "1d",
    top_n: int = 8,
    signal_bars_days: int = 300,
    trace_id: str = "",
    matrix_overrides: Optional[dict[str, Any]] = None,
    constraints: Optional[dict[str, Any]] = None,
    ranking_weights: Optional[dict[str, Any]] = None,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> dict[str, Any]:
    cfg = trader.get_config() if hasattr(trader, "get_config") else {}
    mk = _normalize_research_market(market)
    ml_path = research_ml_matrix_path(mk)
    provider = ResearchProviderRouter(LongPortResearchProvider(trader))
    fetch_bars = getattr(trader, "_fetch_bars", None)
    if not callable(fetch_bars):
        row = {
            "generated_at": datetime.now().isoformat(),
            "trace_id": str(trace_id or ""),
            "market": str(market or "us"),
            "kline": str(kline or "1d"),
            "ok": False,
            "error": "fetch_bars_unavailable",
            "items": [],
        }
        _write_json(ml_path, row)
        return row
    strong = provider.strong_stocks(
        market=str(market or "us"),
        top_n=max(1, int(top_n)),
        kline=str(kline or "1d"),
    )
    symbols = [str(x.get("symbol", "")).strip().upper() for x in strong if isinstance(x, dict)]
    symbols = [s for s in symbols if s][:12]
    if not symbols:
        row = {
            "generated_at": datetime.now().isoformat(),
            "trace_id": str(trace_id or ""),
            "market": str(market or "us"),
            "kline": str(kline or "1d"),
            "ok": False,
            "error": "no_symbol_available",
            "items": [],
        }
        _write_json(ml_path, row)
        return row
    grid = _ml_matrix_param_grid(cfg=cfg if isinstance(cfg, dict) else {}, overrides=matrix_overrides)
    if not grid:
        row = {
            "generated_at": datetime.now().isoformat(),
            "trace_id": str(trace_id or ""),
            "market": str(market or "us"),
            "kline": str(kline or "1d"),
            "ok": False,
            "error": "empty_grid",
            "items": [],
        }
        _write_json(ml_path, row)
        return row
    cst = _ml_matrix_constraints(constraints)
    weights = _ml_matrix_ranking_weights(ranking_weights)
    # build_ml_feature_frame 含 ret_60 + dropna 后需约 ≥80 行；日 K 按「交易日」返回时，
    # 180 个日历日往往只有 ~120～130 根 bar → 净样本仍 <80。日历窗口至少 300 天更稳（约 ≥140 根交易日）。
    _ML_MATRIX_MIN_BARS_DAYS = 300
    _ML_MATRIX_FEATURE_ROWS_MIN = 80
    user_signal_days = _safe_int(signal_bars_days, _ML_MATRIX_MIN_BARS_DAYS)
    bars_days = _clamp_int(signal_bars_days, _ML_MATRIX_MIN_BARS_DAYS, _ML_MATRIX_MIN_BARS_DAYS, 365)
    bars_cache: dict[str, list[Any]] = {}
    feature_cache: dict[tuple[str, int, float], Any] = {}
    items: list[dict[str, Any]] = []
    # 首轮探测：便于排查「有 symbol 但净特征行仍不足」的拉取问题（日 K 按交易日计数）
    _preflight_rows: list[dict[str, Any]] = []
    _tc0 = float(_estimate_label_cost_bps(cfg))
    _hz0 = 5
    for _sym in symbols:
        try:
            _br = fetch_bars(_sym, bars_days, str(kline or "1d"))
        except Exception as _ex:
            bars_cache[_sym] = []
            _preflight_rows.append(
                {"symbol": _sym, "raw_bars": 0, "feature_rows": None, "error": str(_ex)}
            )
            continue
        bars_cache[_sym] = _br or []
        _rn = len(bars_cache[_sym])
        _fr: int | None = None
        _fe: str | None = None
        if bars_cache[_sym]:
            try:
                _dfp = build_ml_feature_frame(
                    bars_cache[_sym],
                    horizon_days=_hz0,
                    transaction_cost_bps=_tc0,
                    symbol=_sym,
                )
                _fr = len(_dfp) if _dfp is not None else None
            except Exception as _ex:
                _fe = str(_ex)
        _preflight_rows.append(
            {
                "symbol": _sym,
                "raw_bars": _rn,
                "feature_rows": _fr,
                "feature_error": _fe,
                "meets_matrix_min": (_fr is not None and _fr >= _ML_MATRIX_FEATURE_ROWS_MIN),
            }
        )

    for idx, g in enumerate(grid):
        if callable(cancel_checker) and bool(cancel_checker()):
            out = {
                "generated_at": datetime.now().isoformat(),
                "trace_id": str(trace_id or ""),
                "market": str(market or "us"),
                "kline": str(kline or "1d"),
                "ok": False,
                "cancelled": True,
                "grid_size": len(grid),
                "evaluated_count": idx,
                "items": items[:100],
            }
            _write_json(ml_path, out)
            return out
        mt = str(g.get("model_type", "logreg")).strip().lower()
        th = _clamp_float(g.get("ml_threshold"), 0.55, 0.5, 0.95)
        hz = _clamp_int(g.get("ml_horizon_days"), 5, 1, 30)
        tr = _clamp_float(g.get("ml_train_ratio"), 0.7, 0.5, 0.9)
        wf = _clamp_int(g.get("ml_walk_forward_windows"), 4, 1, 12)
        tc = _clamp_float(g.get("transaction_cost_bps"), _estimate_label_cost_bps(cfg), 0.0, 100.0)
        symbol_metrics: list[dict[str, Any]] = []
        samples_total = 0
        for sym in symbols:
            if callable(cancel_checker) and bool(cancel_checker()):
                break
            bars = bars_cache.get(sym)
            if bars is None:
                try:
                    bars = fetch_bars(sym, bars_days, str(kline or "1d"))
                except Exception:
                    bars = []
                bars_cache[sym] = bars
            if not bars:
                continue
            fk = (sym, int(hz), float(tc))
            df = feature_cache.get(fk)
            if df is None:
                try:
                    df = build_ml_feature_frame(
                        bars,
                        horizon_days=int(hz),
                        transaction_cost_bps=float(tc),
                        symbol=sym,
                    )
                except Exception:
                    df = None
                feature_cache[fk] = df
            if df is None or len(df) < _ML_MATRIX_FEATURE_ROWS_MIN:
                continue
            wf_row = _walk_forward_eval_with_threshold(
                df=df,
                model_type=mt,
                train_ratio=float(tr),
                max_windows=int(wf),
                threshold=float(th),
                min_train_size=80,
                test_window=20,
            )
            if str(wf_row.get("reason", "")) in {"insufficient_samples", "invalid_split"}:
                continue
            symbol_metrics.append(wf_row)
            samples_total += _safe_int(wf_row.get("samples"), 0)
        if not symbol_metrics:
            items.append(
                {
                    "params": {
                        "model_type": mt,
                        "ml_threshold": round(float(th), 4),
                        "ml_horizon_days": int(hz),
                        "ml_train_ratio": round(float(tr), 4),
                        "ml_walk_forward_windows": int(wf),
                        "transaction_cost_bps": round(float(tc), 4),
                    },
                    "metrics": {"accuracy": 0.0, "precision": 0.0, "recall": 0.0, "coverage": 0.0, "oos_samples": 0},
                    "dataset": {"symbols_used": 0, "symbols_total": len(symbols), "samples": 0},
                    "stability": {"coverage_std": 0.0, "accuracy_std": 0.0, "precision_std": 0.0},
                    "score": 0.0,
                    "pass_constraints": False,
                    "failed_reasons": [
                        "insufficient_samples",
                        "no_symbol_with_80_feature_rows",
                    ],
                }
            )
            continue
        oos_total = sum(_safe_int(x.get("oos_samples"), 0) for x in symbol_metrics)
        sample_total = sum(_safe_int(x.get("samples"), 0) for x in symbol_metrics)
        w_oos = max(1, oos_total)
        w_sample = max(1, sample_total)
        acc = sum(_safe_float(x.get("accuracy"), 0.0) * _safe_int(x.get("oos_samples"), 0) for x in symbol_metrics) / w_oos
        prec = sum(_safe_float(x.get("precision"), 0.0) * _safe_int(x.get("oos_samples"), 0) for x in symbol_metrics) / w_oos
        rec = sum(_safe_float(x.get("recall"), 0.0) * _safe_int(x.get("oos_samples"), 0) for x in symbol_metrics) / w_oos
        cov = sum(_safe_float(x.get("coverage"), 0.0) * _safe_int(x.get("samples"), 0) for x in symbol_metrics) / w_sample
        cov_std = pstdev([_safe_float(x.get("coverage"), 0.0) for x in symbol_metrics]) if len(symbol_metrics) > 1 else 0.0
        acc_std = pstdev([_safe_float(x.get("accuracy"), 0.0) for x in symbol_metrics]) if len(symbol_metrics) > 1 else 0.0
        prec_std = pstdev([_safe_float(x.get("precision"), 0.0) for x in symbol_metrics]) if len(symbol_metrics) > 1 else 0.0
        stability_score = max(0.0, min(1.0, 1.0 - cov_std * 10.0))
        failed_reasons: list[str] = []
        if oos_total < _safe_int(cst.get("min_oos_samples"), 200):
            failed_reasons.append("oos_samples_below_min")
        if cov < _safe_float(cst.get("min_coverage"), 0.05):
            failed_reasons.append("coverage_below_min")
        if prec < _safe_float(cst.get("min_precision"), 0.45):
            failed_reasons.append("precision_below_min")
        if acc < _safe_float(cst.get("min_accuracy"), 0.52):
            failed_reasons.append("accuracy_below_min")
        pass_constraints = len(failed_reasons) == 0
        score = (
            _safe_float(weights.get("precision"), 0.0) * prec
            + _safe_float(weights.get("accuracy"), 0.0) * acc
            + _safe_float(weights.get("recall"), 0.0) * rec
            + _safe_float(weights.get("coverage_stability"), 0.0) * stability_score
        )
        items.append(
            {
                "params": {
                    "model_type": mt,
                    "ml_threshold": round(float(th), 4),
                    "ml_horizon_days": int(hz),
                    "ml_train_ratio": round(float(tr), 4),
                    "ml_walk_forward_windows": int(wf),
                    "transaction_cost_bps": round(float(tc), 4),
                },
                "metrics": {
                    "accuracy": round(float(acc), 4),
                    "precision": round(float(prec), 4),
                    "recall": round(float(rec), 4),
                    "coverage": round(float(cov), 4),
                    "oos_samples": int(oos_total),
                    "windows": int(sum(_safe_int(x.get("windows"), 0) for x in symbol_metrics)),
                },
                "dataset": {
                    "symbols_used": len(symbol_metrics),
                    "symbols_total": len(symbols),
                    "samples": int(samples_total),
                },
                "stability": {
                    "coverage_std": round(float(cov_std), 6),
                    "accuracy_std": round(float(acc_std), 6),
                    "precision_std": round(float(prec_std), 6),
                },
                "score": round(float(score), 6),
                "pass_constraints": bool(pass_constraints),
                "failed_reasons": failed_reasons,
            }
        )
    passed = [x for x in items if bool(x.get("pass_constraints"))]
    passed.sort(key=lambda x: _safe_float(x.get("score"), -9999.0), reverse=True)
    all_sorted = sorted(items, key=lambda x: _safe_float(x.get("score"), -9999.0), reverse=True)
    best_balanced = passed[0] if passed else None
    best_high_precision = (
        max(
            passed,
            key=lambda x: (
                _safe_float((x.get("metrics") or {}).get("precision"), -9999.0),
                _safe_float((x.get("metrics") or {}).get("coverage"), -9999.0),
            ),
        )
        if passed
        else None
    )
    best_high_coverage = (
        max(
            passed,
            key=lambda x: (
                _safe_float((x.get("metrics") or {}).get("coverage"), -9999.0),
                _safe_float((x.get("metrics") or {}).get("precision"), -9999.0),
            ),
        )
        if passed
        else None
    )
    out = {
        "generated_at": datetime.now().isoformat(),
        "trace_id": str(trace_id or ""),
        "market": str(market or "us"),
        "kline": str(kline or "1d"),
        "ok": True,
        "grid_size": len(grid),
        "evaluated_count": len(items),
        "passed_constraints_count": len(passed),
        "top_n": max(1, int(top_n)),
        "signal_bars_days": bars_days,
        "signal_bars_days_requested": user_signal_days,
        "signal_bars_days_note": (
            f"已将 K 线窗口从请求的 {user_signal_days} 天调整为 {bars_days} 天（日K按交易日返回，约需 ≥{ _ML_MATRIX_FEATURE_ROWS_MIN } 行净特征；日历窗口过短则 symbols_used=0、指标全 0）。"
            if bars_days > user_signal_days
            else ""
        ),
        "bar_fetch_preflight": _preflight_rows,
        "constraints": cst,
        "ranking_weights": weights,
        "best_balanced": best_balanced,
        "best_high_precision": best_high_precision,
        "best_high_coverage": best_high_coverage,
        "items": all_sorted[:100],
    }
    _write_json(ml_path, out)
    # 写入 ml_matrix history 快照（每市场每类型最多保留 3 份）
    try:
        mk_n = _history_market_normalize(mk)
        sid = (
            f"mm-{mk_n}-k{str(kline or '1d').strip().lower()}-sig{int(user_signal_days)}"
            f"-top{max(1, int(top_n))}-{_ts_compact_now()}"
        )
        _save_history_snapshot(
            history_type="ml_matrix",
            market=mk_n,
            snapshot_id=sid,
            meta={
                "kline": str(kline or "1d"),
                "top_n": max(1, int(top_n)),
                "signal_bars_days_requested": int(user_signal_days),
                "trace_id": str(trace_id or ""),
                "note": "ml_matrix_snapshot",
            },
            result=out,
        )
    except Exception:
        pass
    return out


def get_research_snapshot() -> dict[str, Any]:
    return _read_json(RESEARCH_SNAPSHOT_FILE)


def get_research_status() -> dict[str, Any]:
    snap = get_research_snapshot()
    has_snapshot = bool(snap)
    return {
        "has_snapshot": has_snapshot,
        "generated_at": snap.get("generated_at") if has_snapshot else None,
        "market": snap.get("market") if has_snapshot else None,
        "kline": snap.get("kline") if has_snapshot else None,
        "top_n": snap.get("top_n") if has_snapshot else None,
        "version": snap.get("version") if has_snapshot else None,
    }


def get_model_compare(top: int = 10) -> dict[str, Any]:
    registry = _read_json(RESEARCH_MODEL_REGISTRY_FILE)
    history = registry.get("history")
    if not isinstance(history, list):
        history = []
    agg: dict[str, dict[str, Any]] = {}
    for row in history:
        if not isinstance(row, dict):
            continue
        name = str(row.get("model_name", "")).strip()
        if not name:
            continue
        score = _safe_float(row.get("avg_composite_score"), float("nan"))
        if score != score:
            score = _safe_float(row.get("metric_score"), float("nan"))
        if score != score:
            score = _safe_float(row.get("wf_accuracy"), -9999)
        rec = agg.setdefault(
            name,
            {
                "model_name": name,
                "runs": 0,
                "avg_score": 0.0,
                "best_score": -9999.0,
                "avg_accuracy": 0.0,
                "avg_precision": 0.0,
                "avg_recall": 0.0,
                "avg_coverage": 0.0,
            },
        )
        rec["runs"] += 1
        rec["avg_score"] += score
        rec["best_score"] = max(_safe_float(rec.get("best_score"), -9999), score)
        rec["avg_accuracy"] += _safe_float(row.get("wf_accuracy"), 0.0)
        rec["avg_precision"] += _safe_float(row.get("wf_precision"), 0.0)
        rec["avg_recall"] += _safe_float(row.get("wf_recall"), 0.0)
        rec["avg_coverage"] += _safe_float(row.get("wf_coverage"), 0.0)
    items = []
    for _, rec in agg.items():
        runs = max(1, int(rec["runs"]))
        items.append(
            {
                "model_name": rec["model_name"],
                "runs": runs,
                "avg_score": round(float(rec["avg_score"]) / runs, 4),
                "best_score": round(float(rec["best_score"]), 4),
                "avg_accuracy": round(float(rec.get("avg_accuracy", 0.0)) / runs, 4),
                "avg_precision": round(float(rec.get("avg_precision", 0.0)) / runs, 4),
                "avg_recall": round(float(rec.get("avg_recall", 0.0)) / runs, 4),
                "avg_coverage": round(float(rec.get("avg_coverage", 0.0)) / runs, 4),
            }
        )
    items.sort(key=lambda x: (x.get("avg_score", -9999), x.get("best_score", -9999)), reverse=True)
    return {"count": len(items), "items": items[: max(1, int(top))]}


def get_factor_ab_report() -> dict[str, Any]:
    row = _read_json(RESEARCH_AB_REPORT_FILE)
    if row:
        return row
    snap = get_research_snapshot()
    report = snap.get("factor_ab_report") if isinstance(snap, dict) else None
    return report if isinstance(report, dict) else {}


def get_factor_ab_report_markdown() -> dict[str, Any]:
    report = get_factor_ab_report()
    md = _ab_report_markdown(report) if report else ""
    if md:
        _write_text(RESEARCH_AB_REPORT_MD_FILE, md)
    return {
        "has_report": bool(report),
        "generated_at": report.get("generated_at") if isinstance(report, dict) else None,
        "markdown": md,
    }


def get_strategy_param_matrix_result(market: Optional[str] = None) -> dict[str, Any]:
    """读取指定市场的策略参数矩阵缓存；若分文件不存在则尝试旧版单文件（仅当其中 market 与请求一致）。"""
    m = _normalize_research_market(market)
    path = research_strategy_matrix_path(m)
    row = _read_json(path)
    if row:
        return row
    leg = _read_json(_RESEARCH_STRATEGY_MATRIX_LEGACY)
    if isinstance(leg, dict) and leg:
        lm = _normalize_research_market(str(leg.get("market", "us")))
        return leg if lm == m else {}
    return {}


def get_ml_param_matrix_result(market: Optional[str] = None) -> dict[str, Any]:
    """读取指定市场的 ML 参数矩阵缓存；若分文件不存在则尝试旧版单文件（仅当其中 market 与请求一致）。"""
    m = _normalize_research_market(market)
    path = research_ml_matrix_path(m)
    row = _read_json(path)
    if row:
        return row
    leg = _read_json(_RESEARCH_ML_MATRIX_LEGACY)
    if isinstance(leg, dict) and leg:
        lm = _normalize_research_market(str(leg.get("market", "us")))
        return leg if lm == m else {}
    return {}


def resolve_ml_matrix_row_for_apply(
    result: dict[str, Any],
    variant: str,
) -> tuple[dict[str, Any] | None, str]:
    """
    从 ML 矩阵结果中选一行用于写入自动交易配置。
    variant: auto | balanced | high_precision | high_coverage | best_score
    返回 (含 params 的 item, 来源标记)。
    """
    if not isinstance(result, dict) or not bool(result.get("ok")):
        return None, ""
    v = str(variant or "auto").strip().lower()
    if v == "balanced":
        x = result.get("best_balanced")
        return (x, "best_balanced") if isinstance(x, dict) else (None, "")
    if v == "high_precision":
        x = result.get("best_high_precision")
        return (x, "best_high_precision") if isinstance(x, dict) else (None, "")
    if v == "high_coverage":
        x = result.get("best_high_coverage")
        return (x, "best_high_coverage") if isinstance(x, dict) else (None, "")
    if v == "best_score":
        items = result.get("items")
        if isinstance(items, list) and items:
            valid = [it for it in items if isinstance(it, dict) and isinstance(it.get("params"), dict)]
            if valid:
                best = max(valid, key=lambda it: _safe_float(it.get("score"), -1e9))
                return best, "items_top_score"
        return None, ""
    # auto：优先通过约束的 best_*，否则按 score 取整表最优
    for key in ("best_balanced", "best_high_precision", "best_high_coverage"):
        x = result.get(key)
        if isinstance(x, dict) and isinstance(x.get("params"), dict):
            return x, key
    items = result.get("items")
    if isinstance(items, list) and items:
        valid = [it for it in items if isinstance(it, dict) and isinstance(it.get("params"), dict)]
        if valid:
            best = max(valid, key=lambda it: _safe_float(it.get("score"), -1e9))
            return best, "items_top_score"
    return None, ""


def ml_matrix_row_to_auto_trader_patch(row: dict[str, Any]) -> dict[str, Any]:
    """将矩阵一行中的 params 转为 update_config 可合并的 ML 字段。"""
    p = row.get("params") if isinstance(row, dict) else None
    if not isinstance(p, dict):
        return {}
    mt = str(p.get("model_type", "logreg")).strip().lower()
    if mt not in {"logreg", "random_forest", "gbdt"}:
        mt = "logreg"
    th = max(0.5, min(_safe_float(p.get("ml_threshold"), 0.55), 0.95))
    hz = max(1, min(_safe_int(p.get("ml_horizon_days"), 5), 30))
    tr = max(0.5, min(_safe_float(p.get("ml_train_ratio"), 0.7), 0.9))
    # 与 AutoTraderService._ml_probability_for_symbol 一致：wf 上限 10
    wf = max(1, min(_safe_int(p.get("ml_walk_forward_windows"), 4), 10))
    return {
        "ml_model_type": mt,
        "ml_threshold": round(th, 4),
        "ml_horizon_days": int(hz),
        "ml_train_ratio": round(tr, 4),
        "ml_walk_forward_windows": int(wf),
    }
