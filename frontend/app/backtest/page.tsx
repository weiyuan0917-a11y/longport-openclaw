"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { apiDelete, apiGet, apiPost } from "@/lib/api";
import { apiBacktestCompare } from "@/lib/backtest-compare-api";
import { FALLBACK_STRATEGY_CATALOG, type StrategyCatalogItem } from "@/lib/backtest-strategy-catalog";
import { LAST_SYMBOL_FALLBACK, readLastSymbol, writeLastSymbol } from "@/lib/last-symbol";
import dynamic from "next/dynamic";
import { PageShell } from "@/components/ui/page-shell";
import { chartAxisStyle, chartPalette, chartTooltipStyle } from "@/lib/chart-theme";
import { buildSwrOptions, SWR_INTERVALS } from "@/lib/swr-config";
import useSWR from "swr";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

/** 与策略注册表 strategy_key 一致；用于列表/图表展示，避免 ADX 过滤双均线与双均线混淆 */
const STRATEGY_KEY_LABEL: Record<string, string> = {
  ma_cross: "双均线",
  adx_ma_filter: "ADX过滤双均线",
  rsi: "RSI",
  macd: "MACD",
  bollinger: "布林带",
  beiming: "北冥有鱼",
  donchian_breakout: "唐奇安突破",
  supertrend: "SuperTrend",
};

const strategyLabel = (name?: string, strategyKey?: string) => {
  const k = String(strategyKey || "").trim().toLowerCase();
  if (k && STRATEGY_KEY_LABEL[k]) return STRATEGY_KEY_LABEL[k];

  const n = String(name || "").toLowerCase();
  if (n.includes("beiming")) return "北冥有鱼";
  // 后端 ADX 策略 __name__ 为 ADX_MA(...)，小写后含 "ma("，必须先识别 adx_ma，不能宽泛匹配 ma(
  if (n.includes("adx_ma")) return "ADX过滤双均线";
  if (n.includes("ma_cross")) return "双均线";
  if (n.includes("donchian")) return "唐奇安突破";
  if (n.includes("supertrend")) return "SuperTrend";
  if (n.includes("rsi")) return "RSI";
  if (n.includes("macd")) return "MACD";
  if (n.includes("bollinger")) return "布林带";
  return name || "-";
};

/** best_curve / best_kline 只有引擎展示名时，从 results 反查 strategy_key */
function strategyKeyForEngineName(results: any[] | undefined, engineName: string | undefined): string | undefined {
  if (!engineName || !results?.length) return undefined;
  const en = String(engineName);
  const row = results.find((r: any) => !r?.error && String(r?.strategy || "") === en);
  const sk = row?.strategy_key;
  return sk != null && String(sk).trim() ? String(sk).trim().toLowerCase() : undefined;
}

// K线周期对应的分钟数
const klineMinutes: Record<string, number> = {
  "1m": 1,
  "5m": 5,
  "10m": 10,
  "30m": 30,
  "1h": 60,
  "2h": 120,
  "4h": 240,
  "1d": 60 * 24,
};

// 计算回测时间描述
const calculateBacktestTime = (periods: number, kline: string): string => {
  const minutes = klineMinutes[kline] || 60 * 24;
  const totalMinutes = periods * minutes;
  
  if (totalMinutes < 60) {
    return `${totalMinutes}分钟`;
  } else if (totalMinutes < 60 * 24) {
    return `${Math.round(totalMinutes / 60 * 10) / 10}小时`;
  } else {
    return `${Math.round(totalMinutes / (60 * 24) * 10) / 10}天`;
  }
};

const toNumber = (v: any): number | null => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const formatFeeBreakdown = (v: any): string => {
  if (!v || typeof v !== "object") return "-";
  const entries = Object.entries(v)
    .filter(([, val]) => Number.isFinite(Number(val)) && Number(val) !== 0)
    .sort((a, b) => Math.abs(Number(b[1])) - Math.abs(Number(a[1])))
    .slice(0, 3)
    .map(([k, val]) => `${k}:${Number(val).toFixed(2)}`);
  return entries.length ? entries.join(" | ") : "-";
};

/** 本地长期缓存（localStorage）；旧版 session 键 v1 会在读取时自动迁移 */
const KLINE_CACHE_PREFIX = "lp_backtest_kline_v2:";
const KLINE_CACHE_LEGACY_SESSION_PREFIX = "lp_backtest_kline_v1:";
const LP_PREF_CACHE_ENABLED = "lp_backtest_kline_cache_enabled";
const LP_PREF_TTL_HOURS = "lp_backtest_kline_ttl_hours";
const KLINE_CACHE_TTL_OPTIONS = [0, 1, 6, 24, 72, 168, 720] as const;
const LEGAL_TTL_HOURS = new Set<number>(KLINE_CACHE_TTL_OPTIONS);

type BarSnapshot = { date: string; open: number; high: number; low: number; close: number; volume?: number };

type KlineCachePack = { v?: number; bars: BarSnapshot[]; savedAt: number };

function klineCacheStorageKey(symbol: string, kline: string, periods: number) {
  return `${KLINE_CACHE_PREFIX}${String(symbol || "").trim().toUpperCase()}:${kline}:${periods}`;
}

function legacySessionCacheKey(symbol: string, kline: string, periods: number) {
  return `${KLINE_CACHE_LEGACY_SESSION_PREFIX}${String(symbol || "").trim().toUpperCase()}:${kline}:${periods}`;
}

function parseKlineCacheRaw(raw: string | null): KlineCachePack | null {
  if (!raw) return null;
  try {
    const p = JSON.parse(raw) as { bars?: BarSnapshot[]; savedAt?: number };
    if (!p || !Array.isArray(p.bars) || !p.bars.length) return null;
    const savedAt = typeof p.savedAt === "number" && Number.isFinite(p.savedAt) ? p.savedAt : Date.now();
    return { v: 2, bars: p.bars, savedAt };
  } catch {
    return null;
  }
}

/** ttlHours=0 表示不按时间过期（仍建议定期点「清除」换新数据） */
function readKlineCache(
  key: string,
  ttlHours: number,
  symbol: string,
  kline: string,
  periods: number,
): BarSnapshot[] | null {
  if (typeof window === "undefined") return null;
  const ttlMs = ttlHours > 0 ? ttlHours * 3600 * 1000 : 0;

  const isStale = (savedAt: number) => ttlMs > 0 && Date.now() - savedAt > ttlMs;

  try {
    const rawL = localStorage.getItem(key);
    const packL = parseKlineCacheRaw(rawL);
    if (packL) {
      if (isStale(packL.savedAt)) {
        try {
          localStorage.removeItem(key);
        } catch {
          /* ignore */
        }
      } else {
        return packL.bars;
      }
    }

    const lk = legacySessionCacheKey(symbol, kline, periods);
    const rawS = typeof sessionStorage !== "undefined" ? sessionStorage.getItem(lk) : null;
    const packS = parseKlineCacheRaw(rawS);
    if (packS) {
      if (isStale(packS.savedAt)) {
        try {
          sessionStorage.removeItem(lk);
        } catch {
          /* ignore */
        }
        return null;
      }
      writeKlineCacheWithSavedAt(key, packS.bars, packS.savedAt);
      try {
        sessionStorage.removeItem(lk);
      } catch {
        /* ignore */
      }
      return packS.bars;
    }
  } catch {
    /* ignore */
  }
  return null;
}

function writeKlineCacheWithSavedAt(key: string, bars: BarSnapshot[], savedAt: number) {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(key, JSON.stringify({ v: 2, bars, savedAt }));
  } catch {
    /* quota / 隐私模式 */
  }
}

function writeKlineCache(key: string, bars: BarSnapshot[]) {
  writeKlineCacheWithSavedAt(key, bars, Date.now());
}

function clearAllLocalKlineCaches() {
  if (typeof localStorage === "undefined") return 0;
  let n = 0;
  try {
    const keys: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const k = localStorage.key(i);
      if (k && k.startsWith(KLINE_CACHE_PREFIX)) keys.push(k);
    }
    for (const k of keys) {
      localStorage.removeItem(k);
      n += 1;
    }
  } catch {
    /* ignore */
  }
  return n;
}

export default function BacktestPage() {
  const [symbol, setSymbol] = useState(LAST_SYMBOL_FALLBACK);
  const [periods, setPeriods] = useState(180);
  const [kline, setKline] = useState("1d");
  const [mlFilterEnabled, setMlFilterEnabled] = useState(false);
  const [mlModelType, setMlModelType] = useState<"logreg" | "random_forest" | "gbdt">("logreg");
  const [mlThreshold, setMlThreshold] = useState(0.55);
  const [mlHorizonDays, setMlHorizonDays] = useState(5);
  const [mlTrainRatio, setMlTrainRatio] = useState(0.7);
  /** 与自动交易 `ml_walk_forward_windows` 对齐；回测 compare 传入 `walk_forward_windows` */
  const [mlWalkForwardWindows, setMlWalkForwardWindows] = useState(1);
  const [mlSyncLoading, setMlSyncLoading] = useState(false);
  const [mlSyncHint, setMlSyncHint] = useState("");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  /** 点击「运行回测」后至接口返回前（请求在后端执行，浏览器等待响应） */
  const [backtestRunning, setBacktestRunning] = useState(false);
  const [tradeModal, setTradeModal] = useState<{
    open: boolean;
    loading: boolean;
    error: string;
    strategy: string;
    strategyKey: string;
    items: any[];
    total: number;
    offset: number;
    limit: number;
  }>({
    open: false,
    loading: false,
    error: "",
    strategy: "",
    strategyKey: "",
    items: [],
    total: 0,
    offset: 0,
    limit: 20,
  });
  const [tradeSort, setTradeSort] = useState<"entry_date_desc" | "entry_date_asc" | "pnl_desc" | "pnl_asc">("entry_date_desc");
  const [exportingAllTrades, setExportingAllTrades] = useState(false);
  const [strategyCatalog, setStrategyCatalog] = useState<StrategyCatalogItem[]>(FALLBACK_STRATEGY_CATALOG);
  const [strategyParams, setStrategyParams] = useState<Record<string, Record<string, number>>>(() => {
    const init: Record<string, Record<string, number>> = {};
    for (const it of FALLBACK_STRATEGY_CATALOG) {
      init[it.name] = { ...it.default_params };
    }
    return init;
  });
  const [strategyDetailsOpen, setStrategyDetailsOpen] = useState(true);
  const [klineCacheEnabled, setKlineCacheEnabled] = useState(true);
  /** 0 = 不按时间过期；其余为有效小时数 */
  const [klineCacheTtlHours, setKlineCacheTtlHours] = useState(24);
  const [prefsReady, setPrefsReady] = useState(false);
  const [cacheHint, setCacheHint] = useState("");
  /** 使用 API 目录 data/klines 下已下载的 K 线回测（需先「下载K线到服务器」） */
  const [useServerKlineCache, setUseServerKlineCache] = useState(false);
  const [serverKlineLoading, setServerKlineLoading] = useState(false);
  const [serverKlineHint, setServerKlineHint] = useState("");
  const latestBarsRef = useRef<BarSnapshot[] | null>(null);
  const { data: strategyCatalogResp, error: strategyCatalogError } = useSWR(
    "/backtest/strategies",
    (path: string) => apiGet<{ items: StrategyCatalogItem[] }>(path),
    buildSwrOptions(SWR_INTERVALS.slowMetadata.refreshInterval, SWR_INTERVALS.slowMetadata.dedupingInterval)
  );

  useLayoutEffect(() => {
    setSymbol(readLastSymbol());
  }, []);

  useEffect(() => {
    try {
      const en = localStorage.getItem(LP_PREF_CACHE_ENABLED);
      if (en === "0") setKlineCacheEnabled(false);
      else if (en === "1") setKlineCacheEnabled(true);
      const th = localStorage.getItem(LP_PREF_TTL_HOURS);
      if (th !== null) {
        const n = Number(th);
        if (Number.isFinite(n) && n >= 0) {
          setKlineCacheTtlHours(LEGAL_TTL_HOURS.has(n) ? n : 24);
        }
      }
    } catch {
      /* ignore */
    }
    setPrefsReady(true);
  }, []);

  useEffect(() => {
    if (!prefsReady) return;
    try {
      localStorage.setItem(LP_PREF_CACHE_ENABLED, klineCacheEnabled ? "1" : "0");
      localStorage.setItem(LP_PREF_TTL_HOURS, String(klineCacheTtlHours));
    } catch {
      /* ignore */
    }
  }, [prefsReady, klineCacheEnabled, klineCacheTtlHours]);

  useEffect(() => {
    const mergeParams = (items: StrategyCatalogItem[]) => {
      setStrategyParams((prev) => {
        const next = { ...prev };
        for (const it of items) {
          if (!next[it.name]) {
            next[it.name] = { ...(it.default_params || {}) };
          }
        }
        return next;
      });
    };
    const raw = Array.isArray(strategyCatalogResp?.items) ? strategyCatalogResp.items : [];
    const normalized = raw
      .filter((it) => it && String(it.name || "").trim())
      .map((it) => ({
        name: String(it.name).trim().toLowerCase(),
        label: String(it.label || it.name || "").trim() || String(it.name),
        description: typeof it.description === "string" ? it.description : undefined,
        default_params: { ...(typeof it.default_params === "object" && it.default_params ? it.default_params : {}) } as Record<
          string,
          number
        >,
      }));
    const items = strategyCatalogError || normalized.length === 0 ? FALLBACK_STRATEGY_CATALOG : normalized;
    setStrategyCatalog(items);
    mergeParams(items);
  }, [strategyCatalogResp, strategyCatalogError]);

  const buildComparePayload = (overrides: Record<string, unknown> = {}) => {
    const sym = String(symbol || "").trim().toUpperCase();
    const base = {
      symbol: sym,
      days: 180,
      periods,
      kline,
      initial_capital: 100000.0,
      execution_mode: "next_open" as const,
      slippage_bps: 3.0,
      commission_bps: null as number | null,
      stamp_duty_bps: null as number | null,
      walk_forward_windows: Math.max(1, Math.min(12, Math.round(Number(mlWalkForwardWindows) || 1))),
      ml_filter_enabled: mlFilterEnabled,
      ml_model_type: mlModelType,
      ml_threshold: mlThreshold,
      ml_horizon_days: mlHorizonDays,
      ml_train_ratio: mlTrainRatio,
      include_trades: false,
      trade_limit: 50,
      trade_offset: 0,
      strategy_key: null as string | null,
      include_best_kline: false,
      bars: undefined as BarSnapshot[] | undefined,
      strategy_params: strategyParams,
      include_bars_in_response: false,
      use_server_kline_cache: false,
    };
    return { ...base, ...overrides };
  };

  const downloadServerKline = async (forceRefresh = false) => {
    const sym = String(symbol || "").trim().toUpperCase();
    if (!sym) {
      setError("请填写标的代码");
      return;
    }
    setServerKlineLoading(true);
    setServerKlineHint("");
    setError("");
    try {
      const r = await apiPost<{
        ok?: boolean;
        bar_count?: number;
        cache_path?: string;
        cached?: boolean;
      }>(
        "/backtest/kline-cache/fetch",
        {
          symbol: sym,
          periods,
          days: 180,
          kline,
          force_refresh: forceRefresh,
        },
        { timeoutMs: 600000, retries: 0 },
      );
      const n = Number(r?.bar_count || 0);
      const p = String(r?.cache_path || "").trim();
      setServerKlineHint(
        r?.cached
          ? `服务器已有缓存（${n} 根）${p ? ` · ${p}` : ""}${forceRefresh ? "（已按强制刷新重新拉取）" : ""}`
          : `已下载并写入服务器（${n} 根）${p ? ` · ${p}` : ""}`,
      );
    } catch (e: any) {
      setServerKlineHint("");
      setError(String(e.message || e));
    } finally {
      setServerKlineLoading(false);
    }
  };

  const deleteServerKline = async () => {
    const sym = String(symbol || "").trim().toUpperCase();
    if (!sym) return;
    setServerKlineLoading(true);
    setError("");
    try {
      const q = new URLSearchParams({
        symbol: sym,
        kline,
        periods: String(periods),
        days: "180",
      });
      await apiDelete(`/backtest/kline-cache?${q.toString()}`, { timeoutMs: 30000, retries: 0 });
      setServerKlineHint("已删除本组服务器 K 线缓存");
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setServerKlineLoading(false);
    }
  };

  const run = async () => {
    setBacktestRunning(true);
    setError("");
    try {
      const ckey = klineCacheStorageKey(symbol, kline, periods);
      const cachedBars = klineCacheEnabled && !useServerKlineCache ? readKlineCache(ckey, klineCacheTtlHours, symbol, kline, periods) : null;
      const needSnapshot =
        useServerKlineCache || (klineCacheEnabled && !cachedBars?.length);
      const payload = buildComparePayload({
        include_best_kline: true,
        include_bars_in_response: needSnapshot,
        use_server_kline_cache: useServerKlineCache,
        ...(useServerKlineCache ? {} : cachedBars?.length ? { bars: cachedBars } : {}),
      });
      const { data: d, usedGetFallback } = await apiBacktestCompare<any>(payload, { timeoutMs: 120000, retries: 0 });
      setData(d);
      writeLastSymbol(symbol);
      if (usedGetFallback) {
        latestBarsRef.current = null;
        setCacheHint(
          "后端未注册 POST /backtest/compare，已自动改用 GET；本次未使用本地 K 线缓存与「策略内参数」。请重启/更新 API（含 main.py 中 @app.post）以恢复完整功能。",
        );
      } else if (useServerKlineCache) {
        latestBarsRef.current = (d?.bars_snapshot as BarSnapshot[]) || null;
        setCacheHint(
          latestBarsRef.current?.length
            ? `已用服务器 K 线缓存回测（附带快照 ${latestBarsRef.current.length} 根供交易明细）`
            : "已用服务器 K 线缓存回测",
        );
      } else if (cachedBars?.length) {
        latestBarsRef.current = cachedBars;
        const ttlNote =
          klineCacheTtlHours > 0 ? `，TTL ${klineCacheTtlHours}h 内有效` : "（未设过期，仅手动清除时失效）";
        setCacheHint(`已用本地缓存（${cachedBars.length} 根）${ttlNote}，未请求行情`);
      } else {
        latestBarsRef.current = (d?.bars_snapshot as BarSnapshot[]) || null;
        if (klineCacheEnabled && d?.bars_snapshot?.length) {
          writeKlineCache(ckey, d.bars_snapshot as BarSnapshot[]);
          const ttlNote =
            klineCacheTtlHours > 0 ? `TTL ${klineCacheTtlHours}h` : "永不过期（仅手动清除）";
          setCacheHint(`已拉取并写入本地缓存（${d.bars_snapshot.length} 根，${ttlNote}），下次同条件跳过行情`);
        } else if (!klineCacheEnabled) {
          latestBarsRef.current = null;
          setCacheHint("缓存已关闭；查看交易明细时将重新拉取K线");
        } else {
          setCacheHint("");
        }
      }
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBacktestRunning(false);
    }
  };

  const syncMlFromAutoTrader = async () => {
    setMlSyncLoading(true);
    setMlSyncHint("");
    setError("");
    try {
      const st = await apiGet<any>("/auto-trader/status");
      const cfg = st?.config;
      if (!cfg || typeof cfg !== "object") {
        throw new Error("未获取到自动交易配置（config 为空）");
      }
      if (typeof cfg.ml_filter_enabled === "boolean") {
        setMlFilterEnabled(cfg.ml_filter_enabled);
      }
      const mt = String(cfg.ml_model_type || "").toLowerCase();
      if (mt === "logreg" || mt === "random_forest" || mt === "gbdt") {
        setMlModelType(mt);
      }
      const th = Number(cfg.ml_threshold);
      if (Number.isFinite(th)) {
        setMlThreshold(Math.max(0.5, Math.min(0.95, th)));
      }
      const hz = Number(cfg.ml_horizon_days);
      if (Number.isFinite(hz)) {
        setMlHorizonDays(Math.max(1, Math.min(30, Math.round(hz))));
      }
      const tr = Number(cfg.ml_train_ratio);
      if (Number.isFinite(tr)) {
        setMlTrainRatio(Math.max(0.5, Math.min(0.9, tr)));
      }
      const wf = Number(cfg.ml_walk_forward_windows);
      if (Number.isFinite(wf)) {
        setMlWalkForwardWindows(Math.max(1, Math.min(12, Math.round(wf))));
      }
      setMlSyncHint("已从自动交易当前配置同步 ML 参数。");
    } catch (e: any) {
      setMlSyncHint("");
      setError(String(e?.message || e));
    } finally {
      setMlSyncLoading(false);
    }
  };

  const clearKlineCache = () => {
    const ckey = klineCacheStorageKey(symbol, kline, periods);
    try {
      localStorage.removeItem(ckey);
    } catch {
      /* ignore */
    }
    try {
      sessionStorage.removeItem(legacySessionCacheKey(symbol, kline, periods));
    } catch {
      /* ignore */
    }
    latestBarsRef.current = null;
    setCacheHint("已清除当前标的+周期的本地缓存");
  };

  const clearAllKlineCaches = () => {
    const n = clearAllLocalKlineCaches();
    latestBarsRef.current = null;
    setCacheHint(`已清除全部本地 K 线缓存（${n} 条）`);
  };

  const updateStrategyParam = (strategyName: string, paramKey: string, value: number) => {
    setStrategyParams((prev) => ({
      ...prev,
      [strategyName]: { ...(prev[strategyName] || {}), [paramKey]: value },
    }));
  };

  const loadTradeDetails = async (row: any, offset = 0) => {
    const key = String(row?.strategy_key || "").trim().toLowerCase();
    if (!key) return;
    setTradeModal((s) => ({
      ...s,
      open: true,
      loading: true,
      error: "",
      strategy: String(row?.strategy || key),
      strategyKey: key,
      offset,
    }));
    try {
      const bars = latestBarsRef.current;
      const payload = buildComparePayload({
        strategy_key: key,
        include_trades: true,
        trade_limit: tradeModal.limit,
        trade_offset: offset,
        include_best_kline: false,
        include_bars_in_response: false,
        use_server_kline_cache: useServerKlineCache,
        ...(useServerKlineCache ? {} : bars?.length ? { bars } : {}),
      });
      const { data: d } = await apiBacktestCompare<any>(payload, { timeoutMs: 120000, retries: 0 });
      const first = (d?.results || [])[0] || {};
      const pg = first?.trades_pagination || {};
      setTradeModal((s) => ({
        ...s,
        open: true,
        loading: false,
        error: "",
        strategy: String(first?.strategy || row?.strategy || key),
        strategyKey: key,
        items: first?.trades || [],
        total: Number(pg.total || 0),
        offset: Number(pg.offset || 0),
      }));
    } catch (e: any) {
      setTradeModal((s) => ({ ...s, loading: false, error: String(e.message || e) }));
    }
  };

  const sortTrades = (
    inputRows: any[],
    sortType: "entry_date_desc" | "entry_date_asc" | "pnl_desc" | "pnl_asc",
  ) => {
    const rows = [...(inputRows || [])];
    const byNum = (v: any) => {
      const n = Number(v);
      return Number.isFinite(n) ? n : 0;
    };
    const byDate = (v: any) => new Date(String(v || "")).getTime() || 0;
    if (sortType === "entry_date_asc") {
      rows.sort((a, b) => byDate(a.entry_date) - byDate(b.entry_date));
    } else if (sortType === "entry_date_desc") {
      rows.sort((a, b) => byDate(b.entry_date) - byDate(a.entry_date));
    } else if (sortType === "pnl_asc") {
      rows.sort((a, b) => byNum(a.pnl) - byNum(b.pnl));
    } else {
      rows.sort((a, b) => byNum(b.pnl) - byNum(a.pnl));
    }
    return rows;
  };
  const sortedTradeItems = sortTrades(tradeModal.items || [], tradeSort);

  const downloadTradesCsv = (rows: any[], fileTag: string) => {
    const headers = ["entry_date", "exit_date", "direction", "quantity", "entry_price", "exit_price", "pnl", "pnl_pct", "hold_days", "symbol"];
    const escapeCsv = (v: any) => {
      const s = String(v ?? "");
      if (s.includes(",") || s.includes("\"") || s.includes("\n")) {
        return `"${s.replaceAll("\"", "\"\"")}"`;
      }
      return s;
    };
    const lines = [
      headers.join(","),
      ...rows.map((r: any) => headers.map((h) => escapeCsv(r[h])).join(",")),
    ];
    const blob = new Blob([`\uFEFF${lines.join("\n")}`], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const key = tradeModal.strategyKey || "strategy";
    const ts = new Date().toISOString().replaceAll(":", "-");
    a.href = url;
    a.download = `backtest-trades-${key}-${fileTag}-${ts}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const exportTradesCsv = () => {
    downloadTradesCsv(sortedTradeItems, "page");
  };

  const exportAllTradesCsv = async () => {
    if (!tradeModal.strategyKey) return;
    setExportingAllTrades(true);
    setTradeModal((s) => ({ ...s, error: "" }));
    try {
      const pageSize = 200;
      let offset = 0;
      let hasMore = true;
      const merged: any[] = [];
      while (hasMore) {
        const bars = latestBarsRef.current;
        const payload = buildComparePayload({
          strategy_key: tradeModal.strategyKey,
          include_trades: true,
          trade_limit: pageSize,
          trade_offset: offset,
          include_best_kline: false,
          include_bars_in_response: false,
          use_server_kline_cache: useServerKlineCache,
          ...(useServerKlineCache ? {} : bars?.length ? { bars } : {}),
        });
        const { data: d } = await apiBacktestCompare<any>(payload, { timeoutMs: 120000, retries: 0 });
        const first = (d?.results || [])[0] || {};
        const rows = Array.isArray(first?.trades) ? first.trades : [];
        const pg = first?.trades_pagination || {};
        merged.push(...rows);
        hasMore = Boolean(pg?.has_more);
        if (!hasMore || rows.length === 0) break;
        offset += pageSize;
      }
      downloadTradesCsv(sortTrades(merged, tradeSort), "all");
    } catch (e: any) {
      setTradeModal((s) => ({ ...s, error: `导出全部失败: ${String(e.message || e)}` }));
    } finally {
      setExportingAllTrades(false);
    }
  };

  const results = data?.results || [];
  const costValues = results
    .map((r: any) => toNumber(r.total_cost_pct_initial))
    .filter((v: number | null): v is number => v !== null);
  const costMin = costValues.length ? Math.min(...costValues) : null;
  const costMax = costValues.length ? Math.max(...costValues) : null;
  const costTextClass = (cost: any) => {
    const c = toNumber(cost);
    if (c === null || costMin === null || costMax === null) return "text-slate-300";
    if (costMax === costMin) return "text-emerald-300";
    const ratio = (c - costMin) / (costMax - costMin);
    if (ratio <= 0.33) return "text-emerald-300";
    if (ratio <= 0.66) return "text-amber-300";
    return "text-rose-300";
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">回测中心</h1>
            <div className="mt-1 text-sm text-slate-300">多策略对比 · 收益回撤分析 · 周期切换回测</div>
          </div>
          <div className="flex gap-2">
            <span className="tag-muted">{symbol}</span>
            <span className="tag-muted">{kline}</span>
            <span className="tag-muted">{periods} 周期</span>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="metric-card">
            <div className="field-label">最佳策略</div>
            <div className="mt-1 text-sm font-semibold text-slate-100">
              {strategyLabel(data?.results?.[0]?.strategy, data?.results?.[0]?.strategy_key)}
            </div>
          </div>
          <div className="metric-card">
            <div className="field-label">最佳收益</div>
            <div className="mt-1 text-xl font-semibold text-emerald-300">{data?.results?.[0]?.total_return_pct ?? "-"}%</div>
          </div>
          <div className="metric-card">
            <div className="field-label">最大回撤</div>
            <div className="mt-1 text-xl font-semibold text-rose-300">{data?.results?.[0]?.max_drawdown_pct ?? "-"}%</div>
          </div>
          <div className="metric-card">
            <div className="field-label">策略数量</div>
            <div className="mt-1 text-xl font-semibold text-slate-200">{(data?.results || []).length}</div>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className={`tag-muted ${mlFilterEnabled ? "text-cyan-200" : ""}`}>
            ML过滤: {mlFilterEnabled ? "开启" : "关闭"}
          </span>
          {mlFilterEnabled ? (
            <>
              <span className="tag-muted">模型: {mlModelType}</span>
              <span className="tag-muted">阈值: {mlThreshold}</span>
              <span className="tag-muted">预测周期: {mlHorizonDays}天</span>
              <span className="tag-muted">训练比例: {mlTrainRatio}</span>
              <span className="tag-muted">走步窗口: {Math.max(1, Math.min(12, Math.round(mlWalkForwardWindows || 1)))}</span>
            </>
          ) : null}
        </div>
      </div>
      <div className="panel toolbar-row">
        <label className="space-y-1">
          <div className="field-label">标的</div>
          <input
            className="input-base"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            onBlur={() => writeLastSymbol(symbol)}
          />
        </label>
        <label className="space-y-1">
          <div className="field-label">周期数</div>
          <input 
            className="input-base w-28" 
            type="number" 
            value={periods} 
            onChange={(e) => setPeriods(Math.max(10, Number(e.target.value) || 10))} 
            min={10}
          />
        </label>
        <label className="space-y-1">
          <div className="field-label">K线周期</div>
          <select className="input-base" value={kline} onChange={(e) => setKline(e.target.value)}>
            <option value="1m">1分K</option>
            <option value="5m">5分K</option>
            <option value="10m">10分K</option>
            <option value="30m">30分K</option>
            <option value="1h">1小时K</option>
            <option value="2h">2小时K</option>
            <option value="4h">4小时K</option>
            <option value="1d">日K</option>
          </select>
        </label>
        <label className="space-y-1">
          <div className="field-label">ML过滤</div>
          <select
            className="input-base"
            value={mlFilterEnabled ? "on" : "off"}
            onChange={(e) => setMlFilterEnabled(e.target.value === "on")}
          >
            <option value="off">关闭</option>
            <option value="on">开启</option>
          </select>
        </label>
        <div className="flex flex-col justify-end gap-1">
          <div className="field-label">同步</div>
          <button
            type="button"
            className="btn-secondary h-9 shrink-0 whitespace-nowrap px-3 text-xs"
            disabled={mlSyncLoading}
            onClick={() => void syncMlFromAutoTrader()}
            title="从 GET /auto-trader/status 中的 config 读取 ML 相关字段"
          >
            {mlSyncLoading ? "同步中…" : "从自动交易同步 ML"}
          </button>
        </div>
        {mlFilterEnabled ? (
          <>
            <label className="space-y-1">
              <div className="field-label">ML模型</div>
              <select
                className="input-base"
                value={mlModelType}
                onChange={(e) => setMlModelType(e.target.value as "logreg" | "random_forest" | "gbdt")}
              >
                <option value="logreg">LogReg</option>
                <option value="random_forest">RandomForest</option>
                <option value="gbdt">GBDT</option>
              </select>
            </label>
            <label className="space-y-1">
              <div className="field-label">阈值</div>
              <input
                className="input-base w-28"
                type="number"
                step={0.01}
                min={0.5}
                max={0.95}
                value={mlThreshold}
                onChange={(e) => setMlThreshold(Math.max(0.5, Math.min(0.95, Number(e.target.value))))}
              />
            </label>
            <label className="space-y-1">
              <div className="field-label">预测周期(天)</div>
              <input
                className="input-base w-28"
                type="number"
                min={1}
                max={30}
                value={mlHorizonDays}
                onChange={(e) => setMlHorizonDays(Math.max(1, Math.min(30, Number(e.target.value))))}
              />
            </label>
            <label className="space-y-1">
              <div className="field-label">训练比例</div>
              <input
                className="input-base w-28"
                type="number"
                step={0.05}
                min={0.5}
                max={0.9}
                value={mlTrainRatio}
                onChange={(e) => setMlTrainRatio(Math.max(0.5, Math.min(0.9, Number(e.target.value))))}
              />
            </label>
            <label className="space-y-1">
              <div className="field-label" title="与自动交易 ml_walk_forward_windows 一致，后端限制 1–12">
                走步窗口
              </div>
              <input
                className="input-base w-28"
                type="number"
                min={1}
                max={12}
                value={mlWalkForwardWindows}
                onChange={(e) =>
                  setMlWalkForwardWindows(Math.max(1, Math.min(12, Math.round(Number(e.target.value) || 1))))
                }
              />
            </label>
          </>
        ) : null}
        <div className="flex flex-wrap items-end gap-2">
          <label className="space-y-1">
            <div className="field-label">K线缓存</div>
            <select
              className="input-base"
              value={klineCacheEnabled ? "on" : "off"}
              onChange={(e) => setKlineCacheEnabled(e.target.value === "on")}
            >
              <option value="on">开启(本地)</option>
              <option value="off">关闭</option>
            </select>
          </label>
          <label className="space-y-1">
            <div className="field-label">有效期</div>
            <select
              className="input-base min-w-[7.5rem]"
              value={String(klineCacheTtlHours)}
              onChange={(e) => setKlineCacheTtlHours(Number(e.target.value))}
              disabled={!klineCacheEnabled}
            >
              <option value="0">永不过期</option>
              <option value="1">1 小时</option>
              <option value="6">6 小时</option>
              <option value="24">24 小时</option>
              <option value="72">3 天</option>
              <option value="168">7 天</option>
              <option value="720">30 天</option>
            </select>
          </label>
          <button type="button" className="btn-secondary h-9 shrink-0" onClick={clearKlineCache}>
            清除本组
          </button>
          <button type="button" className="btn-secondary h-9 shrink-0" onClick={clearAllKlineCaches}>
            清除全部本地
          </button>
        </div>
        <div className="flex w-full basis-full flex-col gap-2 border-t border-slate-700/60 pt-3">
          <div className="text-[11px] uppercase tracking-wide text-slate-500">服务器 K 线（项目目录 data/klines，&gt;1000 根自动分批拉取）</div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-200">
              <input
                type="checkbox"
                checked={useServerKlineCache}
                onChange={(e) => {
                  setUseServerKlineCache(e.target.checked);
                  if (e.target.checked) setServerKlineHint("");
                }}
              />
              回测使用服务器已下载的 K 线
            </label>
            <button
              type="button"
              className="btn-secondary h-9 shrink-0"
              disabled={serverKlineLoading}
              onClick={() => downloadServerKline(false)}
            >
              {serverKlineLoading ? "处理中…" : "下载K线到服务器"}
            </button>
            <button
              type="button"
              className="btn-secondary h-9 shrink-0"
              disabled={serverKlineLoading}
              onClick={() => downloadServerKline(true)}
            >
              强制重新下载
            </button>
            <button type="button" className="btn-secondary h-9 shrink-0" disabled={serverKlineLoading} onClick={deleteServerKline}>
              删除本组服务器缓存
            </button>
          </div>
          {serverKlineHint ? <div className="text-xs text-cyan-200/90">{serverKlineHint}</div> : null}
          {useServerKlineCache ? (
            <div className="text-xs text-amber-200/80">请先点击「下载K线到服务器」再运行回测；开启本项时将忽略浏览器本地 K 线缓存。</div>
          ) : null}
        </div>
        <button type="button" className="btn-primary" onClick={run} disabled={backtestRunning}>
          {backtestRunning ? "正在回测中…" : "运行回测"}
        </button>
        <div className="text-xs text-slate-400 ml-2">
          约 {calculateBacktestTime(periods, kline)}
        </div>
      </div>
      {backtestRunning ? (
        <div className="panel flex items-center gap-3 border border-amber-500/35 bg-amber-950/25 py-2.5 text-sm text-amber-100/95">
          <span
            className="inline-block size-2.5 shrink-0 animate-pulse rounded-full bg-amber-400"
            aria-hidden
          />
          <div>
            <div className="font-medium">正在回测中</div>
            <div className="mt-0.5 text-xs text-amber-200/75">
              请求已提交到后端（拉取 K 线、多策略回测等可能需数十秒～数分钟），请稍候；按钮已暂时禁用以防重复提交。
            </div>
          </div>
        </div>
      ) : null}
      {cacheHint ? (
        <div className="panel border border-cyan-500/25 bg-cyan-950/20 py-2 text-xs text-cyan-100/90">{cacheHint}</div>
      ) : null}
      <details
        className="panel border-slate-700/60 bg-slate-900/40"
        open={strategyDetailsOpen}
        onToggle={(e) => setStrategyDetailsOpen(e.currentTarget.open)}
      >
        <summary className="cursor-pointer select-none text-sm font-medium text-slate-200">
          策略内参数（传给各策略工厂，如均线周期、RSI 阈值等）
        </summary>
        <p className="mt-2 text-xs text-slate-400">
          修改后点击「运行回测」生效。开启 K 线本地缓存时，同一标的+周期+K 线写入浏览器 localStorage，关闭浏览器后仍可用；超过「有效期」会自动重新拉行情。
        </p>
        <div className="mt-3 space-y-4 border-t border-slate-800/80 pt-3">
          {strategyCatalog.length === 0 ? (
            <div className="text-xs text-slate-500">策略列表为空；请检查后端是否提供 GET /backtest/strategies。</div>
          ) : (
            strategyCatalog.map((s) => {
              const pmap = strategyParams[s.name] || s.default_params || {};
              return (
                <div key={s.name} className="rounded-lg border border-slate-800/80 p-3">
                  <div className="text-sm font-semibold text-slate-100">{s.label}</div>
                  <div className="mt-1 text-xs text-slate-500">{s.name}</div>
                  <div className="mt-2 flex flex-wrap gap-3">
                    {Object.keys(pmap).map((pk) => (
                      <label key={pk} className="space-y-1 text-xs">
                        <div className="text-slate-400">{pk}</div>
                        <input
                          type="number"
                          step="any"
                          className="input-base w-24"
                          value={Number.isFinite(strategyParams[s.name]?.[pk]) ? strategyParams[s.name]![pk] : pmap[pk]}
                          onChange={(e) => updateStrategyParam(s.name, pk, Number(e.target.value))}
                        />
                      </label>
                    ))}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </details>
      {error ? <div className="panel border-rose-200 bg-rose-50 text-rose-700">{error}</div> : null}
      {mlSyncHint ? (
        <div className="panel border border-emerald-700/40 bg-emerald-950/30 text-sm text-emerald-200">{mlSyncHint}</div>
      ) : null}
      {data ? (
        <div className="space-y-4">
          <div className="panel">
            <div className="section-title mb-2">策略收益对比</div>
            <ReactECharts
              style={{ height: 320 }}
              option={{
                backgroundColor: "transparent",
                tooltip: chartTooltipStyle,
                xAxis: {
                  type: "category",
                  data: (data.results || []).map((r: any) => strategyLabel(r.strategy, r.strategy_key)),
                  ...chartAxisStyle,
                },
                yAxis: { type: "value", ...chartAxisStyle },
                series: [
                  {
                    name: "总收益%",
                    type: "bar",
                    data: (data.results || []).map((r: any) => r.total_return_pct ?? 0),
                    itemStyle: { color: chartPalette[2] },
                  },
                ],
              }}
            />
          </div>
          {data.best_curve?.points?.length ? (
            <div className="panel">
              <div className="mb-2 text-sm text-slate-300">
                收益曲线 / 回撤曲线（
                {strategyLabel(data.best_curve.strategy, strategyKeyForEngineName(data.results, data.best_curve.strategy))}
                ）
              </div>
              <ReactECharts
                style={{ height: 360 }}
                option={{
                  backgroundColor: "transparent",
                  tooltip: chartTooltipStyle,
                  legend: { data: ["资金曲线", "一直持有", "回撤%"], textStyle: { color: "#475569" } },
                  xAxis: {
                    type: "category",
                    data: (data.best_curve.points || []).map((p: any) => p.date),
                    ...chartAxisStyle,
                    axisLabel: { color: "#64748b", showMinLabel: true, showMaxLabel: true },
                  },
                  yAxis: [
                    { type: "value", name: "资金", ...chartAxisStyle },
                    { type: "value", name: "回撤%", ...chartAxisStyle, axisLabel: { color: "#64748b", formatter: "{value}%" } },
                  ],
                  series: [
                    {
                      name: "资金曲线",
                      type: "line",
                      smooth: true,
                      showSymbol: false,
                      data: (data.best_curve.points || []).map((p: any) => p.equity),
                      lineStyle: { width: 2, color: chartPalette[2] },
                    },
                    {
                      name: "回撤%",
                      type: "line",
                      yAxisIndex: 1,
                      smooth: true,
                      showSymbol: false,
                      data: (data.best_curve.points || []).map((p: any) => p.drawdown_pct),
                      lineStyle: { width: 2, color: chartPalette[4] },
                    },
                    {
                      name: "一直持有",
                      type: "line",
                      smooth: true,
                      showSymbol: false,
                      data: (data.benchmark_curve?.points || []).map((p: any) => p.equity),
                      lineStyle: { width: 2, color: chartPalette[1] },
                    },
                  ],
                }}
              />
            </div>
          ) : null}
          {data.best_kline?.dates?.length ? (
            <div className="panel">
              <div className="mb-2 text-sm text-slate-300">
                最优策略K线交易图（
                {strategyLabel(data.best_kline.strategy, strategyKeyForEngineName(data.results, data.best_kline.strategy))}
                ）
              </div>
              <ReactECharts
                style={{ height: 420 }}
                option={{
                  backgroundColor: "transparent",
                  tooltip: chartTooltipStyle,
                  legend: {
                    data: ["K线", "买点", "卖点"],
                    textStyle: { color: "#475569" },
                  },
                  grid: { left: 55, right: 20, top: 40, bottom: 70 },
                  xAxis: {
                    type: "category",
                    data: data.best_kline.dates || [],
                    axisLabel: { color: "#64748b", showMinLabel: true, showMaxLabel: true },
                    axisLine: { lineStyle: { color: "#d9e3f2" } },
                  },
                  yAxis: {
                    scale: true,
                    axisLabel: { color: "#64748b" },
                    splitLine: { lineStyle: { color: "#e8eef8" } },
                  },
                  dataZoom: [
                    { type: "inside", start: 70, end: 100 },
                    { type: "slider", start: 70, end: 100, bottom: 20 },
                  ],
                  series: [
                    {
                      name: "K线",
                      type: "candlestick",
                      data: data.best_kline.ohlc || [],
                      itemStyle: {
                        color: chartPalette[2],
                        color0: chartPalette[4],
                        borderColor: chartPalette[2],
                        borderColor0: chartPalette[4],
                      },
                    },
                    {
                      name: "买点",
                      type: "scatter",
                      data: (data.best_kline.buy_marks || []).map((m: any) => ({
                        value: [m.date, m.price],
                        quantity: m.quantity,
                      })),
                      symbol: "triangle",
                      symbolSize: 12,
                      itemStyle: { color: chartPalette[2] },
                      tooltip: {
                        formatter: (p: any) =>
                          `买入<br/>日期: ${p?.value?.[0] ?? "-"}<br/>价格: ${p?.value?.[1] ?? "-"}<br/>数量: ${p?.data?.quantity ?? "-"}`,
                      },
                    },
                    {
                      name: "卖点",
                      type: "scatter",
                      data: (data.best_kline.sell_marks || []).map((m: any) => ({
                        value: [m.date, m.price],
                        quantity: m.quantity,
                        pnl: m.pnl,
                        pnl_pct: m.pnl_pct,
                      })),
                      symbol: "pin",
                      symbolSize: 12,
                      itemStyle: { color: chartPalette[4] },
                      tooltip: {
                        formatter: (p: any) =>
                          `卖出<br/>日期: ${p?.value?.[0] ?? "-"}<br/>价格: ${p?.value?.[1] ?? "-"}<br/>数量: ${p?.data?.quantity ?? "-"}<br/>盈亏: ${p?.data?.pnl ?? "-"} (${p?.data?.pnl_pct ?? "-"}%)`,
                      },
                    },
                  ],
                }}
              />
            </div>
          ) : null}
          <div className="grid grid-cols-1 gap-2 md:hidden">
            {(results || []).map((r: any) => (
              <div key={`${r.strategy_key || ""}:${r.strategy}`} className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">
                <div className="font-semibold text-slate-100">{strategyLabel(r.strategy, r.strategy_key)}</div>
                <div className="mt-1 text-emerald-300">总收益: {r.total_return_pct ?? "-"}%</div>
                <div className="text-rose-300">回撤: {r.max_drawdown_pct ?? "-"}%</div>
                <div className="text-slate-300">年化: {r.annual_return_pct ?? "-"}% · 夏普: {r.sharpe_ratio ?? "-"}</div>
                <div className="text-slate-400">胜率: {r.win_rate_pct ?? "-"}% · 交易数: {r.total_trades ?? "-"}</div>
                <div className={costTextClass(r.total_cost_pct_initial)}>
                  成本占比: {r.total_cost_pct_initial ?? "-"}%
                </div>
                <div className="text-xs text-slate-500">成本拆分: {formatFeeBreakdown(r.fee_breakdown)}</div>
                <button className="btn-secondary mt-2" onClick={() => loadTradeDetails(r, 0)}>
                  查看交易明细
                </button>
              </div>
            ))}
          </div>
          <div className="panel hidden md:block">
            <div className="section-title mb-2">策略指标明细</div>
            <div className="table-shell">
              <table className="min-w-full text-sm">
                <thead className="table-head">
                  <tr className="text-left">
                    <th className="px-3 py-2">策略</th>
                    <th className="px-3 py-2">总收益%</th>
                    <th className="px-3 py-2">年化%</th>
                    <th className="px-3 py-2">回撤%</th>
                    <th className="px-3 py-2">夏普</th>
                    <th className="px-3 py-2">胜率%</th>
                    <th className="px-3 py-2">交易数</th>
                    <th className="px-3 py-2">成本占比%</th>
                    <th className="px-3 py-2">成本拆分(Top3)</th>
                    <th className="px-3 py-2">明细</th>
                  </tr>
                </thead>
                <tbody>
                  {(results || []).map((r: any) => (
                    <tr key={`${r.strategy_key || ""}:${r.strategy}`} className="border-t border-slate-800/90 hover:bg-slate-900/40">
                      <td className="px-3 py-2">{strategyLabel(r.strategy, r.strategy_key)}</td>
                      <td className={`px-3 py-2 ${Number(r.total_return_pct) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                        {r.total_return_pct ?? "-"}
                      </td>
                      <td className="px-3 py-2">{r.annual_return_pct ?? "-"}</td>
                      <td className="px-3 py-2 text-rose-300">{r.max_drawdown_pct ?? "-"}</td>
                      <td className="px-3 py-2">{r.sharpe_ratio ?? "-"}</td>
                      <td className="px-3 py-2">{r.win_rate_pct ?? "-"}</td>
                      <td className="px-3 py-2">{r.total_trades ?? "-"}</td>
                      <td className={`px-3 py-2 ${costTextClass(r.total_cost_pct_initial)}`}>
                        {r.total_cost_pct_initial ?? "-"}
                      </td>
                      <td className="px-3 py-2 text-xs text-slate-400">{formatFeeBreakdown(r.fee_breakdown)}</td>
                      <td className="px-3 py-2">
                        <button className="btn-secondary" onClick={() => loadTradeDetails(r, 0)}>
                          查看
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {tradeModal.open ? (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
              <div className="panel max-h-[85vh] w-full max-w-5xl overflow-auto">
                <div className="mb-3 flex items-center justify-between">
                  <div>
                    <div className="text-base font-semibold text-slate-100">
                      交易明细 · {strategyLabel(tradeModal.strategy, tradeModal.strategyKey)}
                    </div>
                    <div className="text-xs text-slate-400">
                      共 {tradeModal.total} 笔，当前 {tradeModal.total > 0 ? tradeModal.offset + 1 : 0} - {Math.min(tradeModal.offset + tradeModal.items.length, tradeModal.total)}
                    </div>
                  </div>
                  <button className="btn-secondary" onClick={() => setTradeModal((s) => ({ ...s, open: false }))}>
                    关闭
                  </button>
                </div>
                <div className="mb-3 flex items-center gap-2">
                  <label className="text-xs text-slate-300">排序</label>
                  <select
                    className="input-base"
                    value={tradeSort}
                    onChange={(e) => setTradeSort(e.target.value as "entry_date_desc" | "entry_date_asc" | "pnl_desc" | "pnl_asc")}
                  >
                    <option value="entry_date_desc">进场时间(新→旧)</option>
                    <option value="entry_date_asc">进场时间(旧→新)</option>
                    <option value="pnl_desc">盈亏(高→低)</option>
                    <option value="pnl_asc">盈亏(低→高)</option>
                  </select>
                  <button className="btn-secondary" onClick={exportTradesCsv} disabled={tradeModal.loading || sortedTradeItems.length === 0}>
                    导出CSV
                  </button>
                  <button
                    className="btn-secondary"
                    onClick={exportAllTradesCsv}
                    disabled={tradeModal.loading || exportingAllTrades || !tradeModal.strategyKey}
                  >
                    {exportingAllTrades ? "导出中..." : "导出全部交易"}
                  </button>
                </div>
                {tradeModal.error ? (
                  <div className="rounded border border-rose-500/40 bg-rose-950/20 p-2 text-rose-300">
                    {tradeModal.error}
                  </div>
                ) : null}
                {tradeModal.loading ? (
                  <div className="text-slate-300">加载中...</div>
                ) : (
                  <div className="table-shell">
                    <table className="min-w-full text-sm">
                      <thead className="table-head">
                        <tr className="text-left">
                          <th className="px-3 py-2">进场日</th>
                          <th className="px-3 py-2">出场日</th>
                          <th className="px-3 py-2">方向</th>
                          <th className="px-3 py-2">数量</th>
                          <th className="px-3 py-2">进场价</th>
                          <th className="px-3 py-2">出场价</th>
                          <th className="px-3 py-2">盈亏</th>
                          <th className="px-3 py-2">盈亏%</th>
                          <th className="px-3 py-2">持有天数</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedTradeItems.map((t: any, idx: number) => (
                          <tr key={`${t.entry_date}-${t.exit_date}-${idx}`} className="border-t border-slate-800/90 hover:bg-slate-900/40">
                            <td className="px-3 py-2">{t.entry_date || "-"}</td>
                            <td className="px-3 py-2">{t.exit_date || "-"}</td>
                            <td className="px-3 py-2">{t.direction || "-"}</td>
                            <td className="px-3 py-2">{t.quantity ?? "-"}</td>
                            <td className="px-3 py-2">{t.entry_price ?? "-"}</td>
                            <td className="px-3 py-2">{t.exit_price ?? "-"}</td>
                            <td className={`px-3 py-2 ${Number(t.pnl) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                              {t.pnl ?? "-"}
                            </td>
                            <td className={`px-3 py-2 ${Number(t.pnl_pct) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                              {t.pnl_pct ?? "-"}
                            </td>
                            <td className="px-3 py-2">{t.hold_days ?? "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div className="mt-3 flex items-center justify-end gap-2">
                  <button
                    className="btn-secondary"
                    disabled={tradeModal.loading || tradeModal.offset <= 0}
                    onClick={() => loadTradeDetails({ strategy_key: tradeModal.strategyKey, strategy: tradeModal.strategy }, Math.max(0, tradeModal.offset - tradeModal.limit))}
                  >
                    上一页
                  </button>
                  <button
                    className="btn-secondary"
                    disabled={tradeModal.loading || tradeModal.offset + tradeModal.limit >= tradeModal.total}
                    onClick={() => loadTradeDetails({ strategy_key: tradeModal.strategyKey, strategy: tradeModal.strategy }, tradeModal.offset + tradeModal.limit)}
                  >
                    下一页
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </PageShell>
  );
}
