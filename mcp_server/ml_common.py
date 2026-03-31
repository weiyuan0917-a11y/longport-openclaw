from __future__ import annotations

from typing import Any, Optional, Sequence


FEATURE_COLUMNS = [
    "ret_3",
    "momentum_5",
    "ret_10",
    "momentum_20",
    "ret_60",
    "volatility_10",
    "volatility_20",
    "downside_vol_20",
    "ma_gap",
    "ma_gap_60",
    "rsi",
    "atr_14",
    "bb_width_20",
    "volume_z_20",
]


def build_ml_feature_frame(
    bars: Sequence[Any],
    horizon_days: int,
    transaction_cost_bps: float = 0.0,
    symbol: Optional[str] = None,
):
    """Build shared ML features from bars for classification tasks."""
    import pandas as pd

    if len(bars) < 80:
        return None

    rows = []
    for b in bars:
        close = float(getattr(b, "close"))
        high = float(getattr(b, "high", close))
        low = float(getattr(b, "low", close))
        raw_volume = getattr(b, "volume", None)
        if raw_volume is None:
            raw_volume = getattr(b, "vol", 0.0)
        rows.append(
            {
                "date": str(getattr(b, "date", "")),
                "close": close,
                "high": high,
                "low": low,
                "volume": float(raw_volume or 0.0),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return None

    df["ret_1"] = df["close"].pct_change()
    df["ret_3"] = df["close"].pct_change(3)
    df["momentum_5"] = df["close"].pct_change(5)
    df["ret_10"] = df["close"].pct_change(10)
    df["momentum_20"] = df["close"].pct_change(20)
    df["ret_60"] = df["close"].pct_change(60)
    df["volatility_10"] = df["ret_1"].rolling(10).std()
    df["volatility_20"] = df["ret_1"].rolling(20).std()
    downside = df["ret_1"].where(df["ret_1"] < 0, 0.0)
    df["downside_vol_20"] = downside.rolling(20).std()
    df["ma_10"] = df["close"].rolling(10).mean()
    df["ma_20"] = df["close"].rolling(20).mean()
    df["ma_60"] = df["close"].rolling(60).mean()
    df["ma_gap"] = (df["ma_10"] - df["ma_20"]) / df["ma_20"]
    df["ma_gap_60"] = (df["close"] - df["ma_60"]) / df["ma_60"]
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    df["rsi"] = 100 - 100 / (1 + rs)
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean() / df["close"]
    std20 = df["close"].rolling(20).std()
    df["bb_width_20"] = (4.0 * std20) / df["ma_20"]
    vol_mean = df["volume"].rolling(20).mean()
    vol_std = df["volume"].rolling(20).std()
    df["volume_z_20"] = (df["volume"] - vol_mean) / vol_std.replace(0, pd.NA)

    hz = max(1, min(int(horizon_days), 30))
    tc = max(0.0, min(float(transaction_cost_bps), 500.0)) / 10000.0
    df["future_ret"] = df["close"].shift(-hz) / df["close"] - 1
    # 以“扣交易成本后的净收益”为监督目标，减少纸面盈利信号。
    df["net_future_ret"] = df["future_ret"] - tc
    df["label"] = (df["net_future_ret"] > 0).astype(int)
    if symbol:
        df["symbol"] = symbol

    df = df.replace([float("inf"), float("-inf")], pd.NA)
    return df.dropna(subset=FEATURE_COLUMNS + ["label"]).reset_index(drop=True)


def walk_forward_probability_map(
    df: Any,
    model_type: str,
    train_ratio: float = 0.7,
    min_train_size: int = 60,
    test_window: int = 20,
    max_windows: int = 6,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Generate out-of-sample probabilities using expanding walk-forward windows."""
    n = len(df)
    if n < max(80, min_train_size + 20):
        return {}, {"enabled": True, "reason": "insufficient_samples", "samples": n}

    ratio = max(0.5, min(float(train_ratio), 0.9))
    split = int(n * ratio)
    split = max(int(min_train_size), min(split, n - 20))
    if split >= n:
        return {}, {"enabled": True, "reason": "invalid_split", "samples": n}

    X = df[FEATURE_COLUMNS].astype(float).values
    y = df["label"].astype(int).values

    test_window = max(10, min(int(test_window), 60))
    max_windows = max(1, min(int(max_windows), 12))

    prob_map: dict[str, float] = {}
    tp = fp = tn = fn = 0
    windows_done = 0

    start = split
    while start < n and windows_done < max_windows:
        end = min(start + test_window, n)
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
        preds = (probs >= 0.5).astype(int)
        for i, p in enumerate(probs):
            prob_map[str(df.iloc[start + i]["date"])] = float(p)
            if preds[i] == 1 and y_test[i] == 1:
                tp += 1
            elif preds[i] == 1 and y_test[i] == 0:
                fp += 1
            elif preds[i] == 0 and y_test[i] == 0:
                tn += 1
            else:
                fn += 1
        windows_done += 1
        start = end

    total = tp + fp + tn + fn
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
    accuracy = ((tp + tn) / total) if total > 0 else 0.0
    coverage = (len(prob_map) / n) if n > 0 else 0.0

    summary = {
        "enabled": True,
        "windows": windows_done,
        "samples": n,
        "oos_samples": total,
        "accuracy": round(float(accuracy), 4),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "coverage": round(float(coverage), 4),
        "split_index": split,
    }
    return prob_map, summary


def create_ml_classifier(model_type: str):
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression

    mt = str(model_type).lower()
    if mt == "random_forest":
        return RandomForestClassifier(
            n_estimators=200,
            max_depth=6,
            min_samples_leaf=5,
            random_state=42,
        )
    if mt == "gbdt":
        return GradientBoostingClassifier(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=3,
            subsample=0.9,
            random_state=42,
        )
    return LogisticRegression(max_iter=1000)
