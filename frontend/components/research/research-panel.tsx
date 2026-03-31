"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type SyntheticEvent } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { formatTime, mapQueueBusyError, INPUT_CLS, PANEL_TITLE_CLS, SUB_TITLE_CLS } from "./research-utils";
import type {
  FactorABMarkdownResult,
  MlMatrixPayload,
  MlMatrixResult,
  ModelCompareResult,
  ResearchSnapshot,
  ResearchStatus,
  StrategyMatrixPayload,
  StrategyMatrixResult,
} from "./types";

type AtCfgSlice = {
  market: "us" | "hk" | "cn";
  kline: "1m" | "5m" | "10m" | "30m" | "1h" | "2h" | "4h" | "1d";
  top_n: number;
  backtest_days: number;
  signal_bars_days: number;
};

type MlMatrixApplyVariant = "auto" | "balanced" | "high_precision" | "high_coverage" | "best_score";
const RESEARCH_UI_CACHE_KEY = "lp_research_panel_cache_v2";

type ResearchUiCache = {
  cfg?: AtCfgSlice | null;
  researchStatus?: ResearchStatus | null;
  researchSnapshot?: ResearchSnapshot | null;
  modelCompare?: ModelCompareResult | null;
  strategyMatrix?: StrategyMatrixPayload | null;
  mlMatrix?: MlMatrixPayload | null;
  abMarkdown?: string;
};

type TaskProgress = {
  taskId: string;
  status: string;
  progressPct: number;
  progressStage: string;
  progressText: string;
  queuePosition: number;
  queueAhead: number;
};

const CACHE_MAX_MODEL_ROWS = 20;
const CACHE_MAX_MATRIX_ROWS = 24;
const CACHE_MAX_STRATEGY_ROWS = 20;
const CACHE_MAX_ALLOC_ROWS = 20;
const CACHE_MAX_AB_ITEMS = 20;
const CACHE_MAX_MD_LEN = 120000;
const MAX_RENDER_ALLOC_ROWS = 120;
const MAX_RENDER_PAIR_POOL_ROWS = 120;
const MAX_RENDER_SELECTED_PAIR_ROWS = 160;
const MAX_RENDER_ML_DIAG_ROWS = 120;
const MAX_RENDER_PAIR_TRADE_ROWS = 300;
const MAX_FILTER_SCAN_PAIR_TRADE_ROWS = 4000;

function normalizeTaskProgress(raw: any, fallbackTaskId: string): TaskProgress {
  const status = String(raw?.status || "").toLowerCase();
  const stage = String(raw?.progress_stage || status || "running");
  const taskId = String(raw?.task_id || fallbackTaskId || "");
  let pct = Number(raw?.progress_pct);
  if (!Number.isFinite(pct)) {
    pct = status === "completed" ? 100 : status === "queued" ? 0 : 10;
  }
  pct = Math.max(0, Math.min(100, Math.round(pct)));
  const text =
    String(raw?.progress_text || "").trim() ||
    (status === "completed"
      ? "任务完成"
      : status === "failed"
        ? "任务失败"
        : status === "cancelled"
          ? "任务已取消"
          : status === "queued"
            ? "任务排队中"
            : "任务运行中");
  const queuePosition = Math.max(0, Number(raw?.queue_position || 0) || 0);
  const queueAhead = Math.max(0, Number(raw?.queue_ahead || (queuePosition > 0 ? queuePosition - 1 : 0)) || 0);
  return {
    taskId,
    status,
    progressPct: pct,
    progressStage: stage,
    progressText: text,
    queuePosition,
    queueAhead,
  };
}

function pickLatestTaskByType(tasks: any[], taskType: string): any | null {
  const rows = tasks.filter((x) => String(x?.task_type || "").toLowerCase() === taskType);
  if (!rows.length) return null;
  rows.sort((a, b) => {
    const as = String(a?.started_at || a?.created_at || "");
    const bs = String(b?.started_at || b?.created_at || "");
    return bs.localeCompare(as);
  });
  return rows[0] || null;
}

function isTransientRequestError(err: any): boolean {
  const msg = String(err?.message || err || "").toLowerCase();
  return (
    msg.includes("请求超时") ||
    msg.includes("failed to fetch") ||
    msg.includes("networkerror") ||
    msg.includes("aborterror")
  );
}

async function recoverAcceptedTask(taskType: "research" | "strategy_matrix" | "ml_matrix"): Promise<any | null> {
  try {
    const rs = await apiGet<ResearchStatus>("/auto-trader/research/status", {
      timeoutMs: 10000,
      retries: 0,
    });
    const activeTasks = Array.isArray((rs as any)?.task_queue?.active_tasks)
      ? ((rs as any).task_queue.active_tasks as any[])
      : [];
    const picked = pickLatestTaskByType(activeTasks, taskType);
    if (!picked) return null;
    const taskId = String(picked?.task_id || "");
    if (!taskId) return null;
    return {
      ok: true,
      accepted: true,
      mode: "async",
      task_id: taskId,
      message: "recovered_after_timeout",
    };
  } catch {
    return null;
  }
}

function readResearchUiCache(): ResearchUiCache | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(RESEARCH_UI_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as ResearchUiCache) : null;
  } catch {
    return null;
  }
}

function writeResearchUiCache(cache: ResearchUiCache): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(RESEARCH_UI_CACHE_KEY, JSON.stringify(cache));
  } catch {
    // ignore cache write errors
  }
}

function trimResearchCache(cache: ResearchUiCache): ResearchUiCache {
  const out: ResearchUiCache = {
    cfg: cache.cfg ?? null,
    researchStatus: cache.researchStatus ?? null,
    researchSnapshot: cache.researchSnapshot ?? null,
    modelCompare: cache.modelCompare ?? null,
    strategyMatrix: cache.strategyMatrix ?? null,
    mlMatrix: cache.mlMatrix ?? null,
    abMarkdown: typeof cache.abMarkdown === "string" ? cache.abMarkdown.slice(0, CACHE_MAX_MD_LEN) : "",
  };

  if (out.modelCompare?.items && Array.isArray(out.modelCompare.items)) {
    out.modelCompare = { ...out.modelCompare, items: out.modelCompare.items.slice(0, CACHE_MAX_MODEL_ROWS) };
  }
  if (out.strategyMatrix?.items && Array.isArray(out.strategyMatrix.items)) {
    out.strategyMatrix = { ...out.strategyMatrix, items: out.strategyMatrix.items.slice(0, CACHE_MAX_MATRIX_ROWS) };
  }
  if (out.mlMatrix?.items && Array.isArray(out.mlMatrix.items)) {
    out.mlMatrix = { ...out.mlMatrix, items: out.mlMatrix.items.slice(0, CACHE_MAX_MATRIX_ROWS) };
  }

  const snap = out.researchSnapshot?.snapshot;
  if (snap && typeof snap === "object") {
    const trimmedSnapshot: any = { ...snap };
    if (Array.isArray(trimmedSnapshot.strategy_rankings)) {
      trimmedSnapshot.strategy_rankings = trimmedSnapshot.strategy_rankings.slice(0, CACHE_MAX_STRATEGY_ROWS);
    }
    if (Array.isArray(trimmedSnapshot.allocation_plan)) {
      trimmedSnapshot.allocation_plan = trimmedSnapshot.allocation_plan.slice(0, CACHE_MAX_ALLOC_ROWS);
    }
    if (trimmedSnapshot.factor_ab_report && typeof trimmedSnapshot.factor_ab_report === "object") {
      const ab = { ...trimmedSnapshot.factor_ab_report };
      if (Array.isArray(ab.items)) ab.items = ab.items.slice(0, CACHE_MAX_AB_ITEMS);
      trimmedSnapshot.factor_ab_report = ab;
    }
    // 交易明细可能很大，缓存中移除，避免切页卡顿；页面可后台重新拉取。
    if (trimmedSnapshot.pair_backtest && typeof trimmedSnapshot.pair_backtest === "object") {
      trimmedSnapshot.pair_backtest = {
        ...trimmedSnapshot.pair_backtest,
        selected_pairs: [],
      };
    }
    out.researchSnapshot = {
      ...(out.researchSnapshot as any),
      snapshot: trimmedSnapshot,
    };
  }
  return out;
}
type StrategyMatrixPresetKey = "conservative" | "balanced" | "aggressive";

const STRATEGY_MATRIX_PRESETS: Record<
  StrategyMatrixPresetKey,
  {
    label: string;
    top_n: number;
    max_strategies: number;
    max_drawdown_limit_pct: number;
    min_symbols_used: number;
    matrix_overrides: Record<string, unknown>;
  }
> = {
  conservative: {
    label: "保守（最快）",
    top_n: 6,
    max_strategies: 4,
    max_drawdown_limit_pct: 25,
    min_symbols_used: 3,
    matrix_overrides: {
      use_config_strategies_only: true,
      parallel_workers: 4,
      backtest_days: 90,
      max_total_variants: 80,
      max_variants_per_strategy: 6,
      max_eval_cache_entries: 30000,
    },
  },
  balanced: {
    label: "平衡（推荐）",
    top_n: 8,
    max_strategies: 6,
    max_drawdown_limit_pct: 30,
    min_symbols_used: 4,
    matrix_overrides: {
      use_config_strategies_only: true,
      parallel_workers: 6,
      backtest_days: 120,
      max_total_variants: 160,
      max_variants_per_strategy: 10,
      max_eval_cache_entries: 50000,
    },
  },
  aggressive: {
    label: "激进（更全面）",
    top_n: 10,
    max_strategies: 8,
    max_drawdown_limit_pct: 35,
    min_symbols_used: 4,
    matrix_overrides: {
      use_config_strategies_only: false,
      parallel_workers: 8,
      backtest_days: 180,
      max_total_variants: 320,
      max_variants_per_strategy: 16,
      max_eval_cache_entries: 80000,
    },
  },
};

type ResearchPanelOpenSnapshot = {
  pairBacktestPanelOpen: boolean;
  allocationPanelOpen: boolean;
  abPanelOpen: boolean;
  mlDiagPanelOpen: boolean;
  modelComparePanelOpen: boolean;
  strategyMatrixPanelOpen: boolean;
  strategySummaryPanelOpen: boolean;
  mlMatrixPanelOpen: boolean;
};

const RESEARCH_PANEL_OVERRIDE_KEYS: Record<string, keyof ResearchPanelOpenSnapshot> = {
  model_compare: "modelComparePanelOpen",
  strategy_summary: "strategySummaryPanelOpen",
  strategy_matrix: "strategyMatrixPanelOpen",
  ml_matrix: "mlMatrixPanelOpen",
  ml_diag: "mlDiagPanelOpen",
  ab_report: "abPanelOpen",
  allocation: "allocationPanelOpen",
  pair_backtest: "pairBacktestPanelOpen",
};

export function ResearchPanel() {
  const [cachedSeed] = useState<ResearchUiCache | null>(() => readResearchUiCache());
  const [error, setError] = useState("");
  const loadingRef = useRef(false);
  const [cfg, setCfg] = useState<AtCfgSlice | null>(cachedSeed?.cfg ?? null);
  const [researchStatus, setResearchStatus] = useState<ResearchStatus | null>(cachedSeed?.researchStatus ?? null);
  const [researchSnapshot, setResearchSnapshot] = useState<ResearchSnapshot | null>(cachedSeed?.researchSnapshot ?? null);
  const [modelCompare, setModelCompare] = useState<ModelCompareResult | null>(cachedSeed?.modelCompare ?? null);
  const [strategyMatrix, setStrategyMatrix] = useState<StrategyMatrixPayload | null>(cachedSeed?.strategyMatrix ?? null);
  const [mlMatrix, setMlMatrix] = useState<MlMatrixPayload | null>(cachedSeed?.mlMatrix ?? null);
  const [researchRunning, setResearchRunning] = useState(false);
  const [strategyMatrixRunning, setStrategyMatrixRunning] = useState(false);
  const [mlMatrixRunning, setMlMatrixRunning] = useState(false);
  const [researchTaskId, setResearchTaskId] = useState<string>("");
  const [strategyMatrixTaskId, setStrategyMatrixTaskId] = useState<string>("");
  const [mlMatrixTaskId, setMlMatrixTaskId] = useState<string>("");
  const [researchProgress, setResearchProgress] = useState<TaskProgress | null>(null);
  const [strategyMatrixProgress, setStrategyMatrixProgress] = useState<TaskProgress | null>(null);
  const [mlMatrixProgress, setMlMatrixProgress] = useState<TaskProgress | null>(null);
  const [mlApplyVariant, setMlApplyVariant] = useState<MlMatrixApplyVariant>("auto");
  const [mlMatrixSnapshots, setMlMatrixSnapshots] = useState<any[]>([]);
  // 空字符串表示使用“最新结果”（后端兼容逻辑）。
  const [mlApplySnapshotId, setMlApplySnapshotId] = useState<string>("");
  const [strategyPreset, setStrategyPreset] = useState<StrategyMatrixPresetKey>("balanced");
  const [mlApplyBusy, setMlApplyBusy] = useState(false);
  const [abMarkdown, setAbMarkdown] = useState<string>(String(cachedSeed?.abMarkdown || ""));
  const [pairTradeFilterPair, setPairTradeFilterPair] = useState("");
  const [pairTradeFilterSymbol, setPairTradeFilterSymbol] = useState("");
  const [strategyMatrixPanelOpen, setStrategyMatrixPanelOpen] = useState(false);
  const [mlMatrixPanelOpen, setMlMatrixPanelOpen] = useState(false);
  const [mlDiagPanelOpen, setMlDiagPanelOpen] = useState(false);
  const [abPanelOpen, setAbPanelOpen] = useState(false);
  const [allocationPanelOpen, setAllocationPanelOpen] = useState(false);
  const [pairBacktestPanelOpen, setPairBacktestPanelOpen] = useState(false);
  const [modelComparePanelOpen, setModelComparePanelOpen] = useState(false);
  const [strategySummaryPanelOpen, setStrategySummaryPanelOpen] = useState(false);
  /** 折叠块展开后触发的按需刷新：显示「正在加载」且不依赖 loadResearch 引用变化 */
  const [sectionLoading, setSectionLoading] = useState<Record<string, boolean>>({});

  const panelOpenRef = useRef<ResearchPanelOpenSnapshot>({
    pairBacktestPanelOpen: false,
    allocationPanelOpen: false,
    abPanelOpen: false,
    mlDiagPanelOpen: false,
    modelComparePanelOpen: false,
    strategyMatrixPanelOpen: false,
    strategySummaryPanelOpen: false,
    mlMatrixPanelOpen: false,
  });
  useEffect(() => {
    panelOpenRef.current = {
      pairBacktestPanelOpen,
      allocationPanelOpen,
      abPanelOpen,
      mlDiagPanelOpen,
      modelComparePanelOpen,
      strategyMatrixPanelOpen,
      strategySummaryPanelOpen,
      mlMatrixPanelOpen,
    };
  }, [
    pairBacktestPanelOpen,
    allocationPanelOpen,
    abPanelOpen,
    mlDiagPanelOpen,
    modelComparePanelOpen,
    strategyMatrixPanelOpen,
    strategySummaryPanelOpen,
    mlMatrixPanelOpen,
  ]);

  const taskRunningRef = useRef({
    researchRunning: false,
    strategyMatrixRunning: false,
    mlMatrixRunning: false,
  });
  useEffect(() => {
    taskRunningRef.current = {
      researchRunning,
      strategyMatrixRunning,
      mlMatrixRunning,
    };
  }, [researchRunning, strategyMatrixRunning, mlMatrixRunning]);

  const latestStateRef = useRef<{
    cfg: AtCfgSlice | null;
    researchStatus: ResearchStatus | null;
    researchSnapshot: ResearchSnapshot | null;
    modelCompare: ModelCompareResult | null;
    strategyMatrix: StrategyMatrixPayload | null;
    mlMatrix: MlMatrixPayload | null;
    abMarkdown: string;
  }>({
    cfg,
    researchStatus,
    researchSnapshot,
    modelCompare,
    strategyMatrix,
    mlMatrix,
    abMarkdown,
  });

  useEffect(() => {
    latestStateRef.current = {
      cfg,
      researchStatus,
      researchSnapshot,
      modelCompare,
      strategyMatrix,
      mlMatrix,
      abMarkdown,
    };
  }, [cfg, researchStatus, researchSnapshot, modelCompare, strategyMatrix, mlMatrix, abMarkdown]);

  // ML 矩阵 history 列表：仅在 ML 面板打开时拉取
  useEffect(() => {
    if (!cfg?.market) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiGet<any>(
          `/auto-trader/research/snapshots?type=ml_matrix&market=${encodeURIComponent(cfg.market)}`,
          { timeoutMs: 12000, retries: 0 }
        );
        const rows = Array.isArray(res?.snapshots) ? res.snapshots : [];
        if (!cancelled) setMlMatrixSnapshots(rows);
      } catch {
        // ignore: 默认使用“最新结果”
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cfg?.market, mlMatrix?.generated_at]);

  const buildPairTradeRows = useCallback((rows: any[], maxRows?: number) => {
    const out: any[] = [];
    for (const row of rows) {
      const pair = row?.pair || {};
      const metrics = row?.selected_metrics || {};
      const symbol = row?.selected_symbol || metrics?.symbol || "-";
      const strategy = metrics?.strategy_label || row?.selected_strategy || "-";
      const trades = Array.isArray(metrics?.trade_history) ? metrics.trade_history : [];
      for (let idx = 0; idx < trades.length; idx += 1) {
        const t = trades[idx];
        out.push({
          id: `${pair?.long || "long"}-${pair?.short || "short"}-${symbol}-${idx}`,
          pair: `${pair?.long || "-"} / ${pair?.short || "-"}`,
          symbol,
          strategy,
          entry_date: t?.entry_date,
          exit_date: t?.exit_date,
          entry_price: t?.entry_price,
          exit_price: t?.exit_price,
          quantity: t?.quantity,
          pnl_pct: t?.pnl_pct,
          pnl: t?.pnl,
          hold_days: t?.hold_days,
        });
        if (typeof maxRows === "number" && maxRows > 0 && out.length >= maxRows) {
          return out;
        }
      }
    }
    return out;
  }, []);

  const loadResearch = useCallback(
    async (
      force = false,
      opts?: { retainError?: boolean; panelOverrides?: Partial<ResearchPanelOpenSnapshot> }
    ) => {
    const retainError = Boolean(opts?.retainError);
    if ((loadingRef.current && !force) || (!force && typeof document !== "undefined" && document.hidden)) {
      return;
    }
    loadingRef.current = true;
    try {
      const latest = latestStateRef.current;
      const panels: ResearchPanelOpenSnapshot = { ...panelOpenRef.current, ...(opts?.panelOverrides || {}) };
      const tr = taskRunningRef.current;
      const heavyTaskRunning = tr.strategyMatrixRunning || tr.mlMatrixRunning || tr.researchRunning;

      const [statusResult, rsResult] = await Promise.allSettled([
        apiGet<any>("/auto-trader/status", {
          timeoutMs: 10000,
          retries: 0,
        }),
        apiGet<ResearchStatus>("/auto-trader/research/status", {
          timeoutMs: 10000,
          retries: 0,
        }),
      ]);

      const st = statusResult.status === "fulfilled" ? statusResult.value : null;
      const c = st?.config;
      const nextCfg: AtCfgSlice | null = c
        ? {
            market: (c.market as AtCfgSlice["market"]) || "us",
            kline: (c.kline as AtCfgSlice["kline"]) || "1d",
            top_n: Number(c.top_n) || 8,
            backtest_days: Number(c.backtest_days) || 120,
            signal_bars_days: Number(c.signal_bars_days) || 90,
          }
        : latest.cfg;
      if (c) {
        setCfg(nextCfg);
      } else if (statusResult.status === "fulfilled") {
        setCfg(null);
      }

      const mkt = c ? String(c.market || "us") : String(nextCfg?.market || "us");
      let nextResearchStatus: ResearchStatus | null = latest.researchStatus;
      let nextResearchSnapshot: ResearchSnapshot | null = latest.researchSnapshot;
      let nextModelCompare: ModelCompareResult | null = latest.modelCompare;
      let nextStrategyMatrix: StrategyMatrixPayload | null = latest.strategyMatrix;
      let nextMlMatrix: MlMatrixPayload | null = latest.mlMatrix;

      const shouldFetchSnapshot =
        force ||
        (!heavyTaskRunning &&
          (panels.pairBacktestPanelOpen ||
            panels.allocationPanelOpen ||
            panels.abPanelOpen ||
            panels.mlDiagPanelOpen));
      const shouldFetchModelCompare = force || (!heavyTaskRunning && panels.modelComparePanelOpen);
      const shouldFetchStrategyMatrix =
        force || (!heavyTaskRunning && (panels.strategyMatrixPanelOpen || panels.strategySummaryPanelOpen));
      const shouldFetchMlMatrix = force || (!heavyTaskRunning && panels.mlMatrixPanelOpen);

      if (rsResult.status === "fulfilled") {
        try {
          const rs = rsResult.value;
          nextResearchStatus = rs || null;
          setResearchStatus(nextResearchStatus);
          const activeTasks = Array.isArray((rs as any)?.task_queue?.active_tasks)
            ? ((rs as any).task_queue.active_tasks as any[])
            : [];
          const researchTask = pickLatestTaskByType(activeTasks, "research");
          const strategyTask = pickLatestTaskByType(activeTasks, "strategy_matrix");
          const mlTask = pickLatestTaskByType(activeTasks, "ml_matrix");

          if (researchTask) {
            const tid = String(researchTask?.task_id || "");
            if (tid) setResearchTaskId(tid);
            setResearchRunning(true);
            setResearchProgress(normalizeTaskProgress(researchTask, tid));
          }
          if (strategyTask) {
            const tid = String(strategyTask?.task_id || "");
            if (tid) setStrategyMatrixTaskId(tid);
            setStrategyMatrixRunning(true);
            setStrategyMatrixProgress(normalizeTaskProgress(strategyTask, tid));
          }
          if (mlTask) {
            const tid = String(mlTask?.task_id || "");
            if (tid) setMlMatrixTaskId(tid);
            setMlMatrixRunning(true);
            setMlMatrixProgress(normalizeTaskProgress(mlTask, tid));
          }
        } catch {
          // 保留上次状态，等待下一轮刷新
        }
      }

      try {
        const results = await Promise.allSettled([
          shouldFetchSnapshot
            ? apiGet<ResearchSnapshot>("/auto-trader/research/snapshot")
            : Promise.resolve(nextResearchSnapshot),
          shouldFetchModelCompare
            ? apiGet<ModelCompareResult>("/auto-trader/research/model-compare?top=10")
            : Promise.resolve(nextModelCompare),
          shouldFetchStrategyMatrix
            ? apiGet<StrategyMatrixResult>(
                `/auto-trader/research/strategy-matrix/result?market=${encodeURIComponent(mkt)}`
              )
            : Promise.resolve(nextStrategyMatrix ? { ok: true, result: nextStrategyMatrix } : null),
          shouldFetchMlMatrix
            ? apiGet<MlMatrixResult>(`/auto-trader/research/ml-matrix/result?market=${encodeURIComponent(mkt)}`)
            : Promise.resolve(nextMlMatrix ? { ok: true, result: nextMlMatrix } : null),
        ]);

        const [snapRes, mcRes, smRes, mmRes] = results;
        if (shouldFetchSnapshot && snapRes.status === "fulfilled") {
          nextResearchSnapshot = (snapRes.value as ResearchSnapshot) || null;
          setResearchSnapshot(nextResearchSnapshot);
        }
        if (shouldFetchModelCompare && mcRes.status === "fulfilled") {
          nextModelCompare = (mcRes.value as ModelCompareResult) || null;
          setModelCompare(nextModelCompare);
        }
        if (shouldFetchStrategyMatrix && smRes.status === "fulfilled") {
          nextStrategyMatrix = ((smRes.value as StrategyMatrixResult | null)?.result as StrategyMatrixPayload) || null;
          setStrategyMatrix(nextStrategyMatrix);
        }
        if (shouldFetchMlMatrix && mmRes.status === "fulfilled") {
          nextMlMatrix = ((mmRes.value as MlMatrixResult | null)?.result as MlMatrixPayload) || null;
          setMlMatrix(nextMlMatrix);
        }
      } catch {
        // 保留上次缓存/已展示数据，避免切页后出现“整块清空再加载”
      }
      let nextAbMarkdown = latest.abMarkdown;
      if (!heavyTaskRunning && (force || panels.abPanelOpen)) {
        try {
          const md = await apiGet<FactorABMarkdownResult>("/auto-trader/research/ab-report/markdown");
          nextAbMarkdown = String(md?.markdown || "");
          setAbMarkdown(nextAbMarkdown);
        } catch {
          // 保留上次 markdown，避免后台繁忙时反复清空
        }
      }
      writeResearchUiCache(
        trimResearchCache({
          cfg: nextCfg,
          researchStatus: nextResearchStatus,
          researchSnapshot: nextResearchSnapshot,
          modelCompare: nextModelCompare,
          strategyMatrix: nextStrategyMatrix,
          mlMatrix: nextMlMatrix,
          abMarkdown: nextAbMarkdown,
        })
      );
      if (!retainError) {
        setError("");
      }
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      loadingRef.current = false;
    }
  }, []);

  const onResearchPanelToggle = useCallback(
    (setter: (open: boolean) => void, sectionKey: string) => (e: SyntheticEvent<HTMLDetailsElement>) => {
      const open = e.currentTarget.open;
      setter(open);
      if (open) {
        const k = RESEARCH_PANEL_OVERRIDE_KEYS[sectionKey];
        const panelOverrides = k ? ({ [k]: true } as Partial<ResearchPanelOpenSnapshot>) : undefined;
        setSectionLoading((s) => ({ ...s, [sectionKey]: true }));
        void loadResearch(true, { panelOverrides }).finally(() =>
          setSectionLoading((s) => ({ ...s, [sectionKey]: false }))
        );
      }
    },
    [loadResearch]
  );

  useEffect(() => {
    void loadResearch(true);
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const scheduleNext = () => {
      if (disposed) return;
      const heavyRunning = strategyMatrixRunning || mlMatrixRunning || researchRunning;
      const visible = typeof document === "undefined" ? true : !document.hidden;
      const intervalMs = heavyRunning ? (visible ? 5000 : 15000) : visible ? 30000 : 120000;
      timer = setTimeout(async () => {
        await loadResearch(false);
        scheduleNext();
      }, intervalMs);
    };

    scheduleNext();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [loadResearch, strategyMatrixRunning, mlMatrixRunning, researchRunning]);

  useEffect(() => {
    const q: any = researchStatus?.task_queue;
    if (!q) return;
    const queued = Number(q?.queued ?? 0);
    const running = Number(q?.running ?? 0);
    if (queued + running > 0) return;
    // 后端队列已空时，自动清理前端本地“运行中”残留态。
    if (researchRunning) setResearchRunning(false);
    if (strategyMatrixRunning) setStrategyMatrixRunning(false);
    if (mlMatrixRunning) setMlMatrixRunning(false);
    if (researchTaskId) setResearchTaskId("");
    if (strategyMatrixTaskId) setStrategyMatrixTaskId("");
    if (mlMatrixTaskId) setMlMatrixTaskId("");
    if (researchProgress) setResearchProgress(null);
    if (strategyMatrixProgress) setStrategyMatrixProgress(null);
    if (mlMatrixProgress) setMlMatrixProgress(null);
  }, [
    researchStatus,
    researchRunning,
    strategyMatrixRunning,
    mlMatrixRunning,
    researchTaskId,
    strategyMatrixTaskId,
    mlMatrixTaskId,
    researchProgress,
    strategyMatrixProgress,
    mlMatrixProgress,
  ]);

  const runResearch = async () => {
    if (!cfg) return;
    let trackingTaskId = "";
    try {
      let accepted: any = null;
      try {
        accepted = await apiPost<any>(
          "/auto-trader/research/run",
          {
            market: cfg.market,
            kline: cfg.kline,
            top_n: cfg.top_n,
            backtest_days: cfg.backtest_days,
            async_run: true,
          },
          {
            timeoutMs: 15000,
            retries: 0,
          }
        );
      } catch (startErr: any) {
        if (!isTransientRequestError(startErr)) throw startErr;
        const recovered = await recoverAcceptedTask("research");
        if (!recovered) throw startErr;
        accepted = recovered;
        setError("Research 启动请求超时，已自动接管后台任务并继续跟踪进度。");
      }
      trackingTaskId = String(accepted?.task_id || "").trim();
      if (trackingTaskId) {
        setResearchRunning(true);
        setResearchTaskId(trackingTaskId);
        setResearchProgress({
          taskId: trackingTaskId,
          status: "queued",
          progressPct: 0,
          progressStage: "queued",
          progressText: "任务排队中",
          queuePosition: 0,
          queueAhead: 0,
        });
      }
      await loadResearch(true);
      setError("");
    } catch (e: any) {
      const queueMsg = mapQueueBusyError(e, "Research");
      setError(queueMsg || String(e.message || e));
      setResearchRunning(false);
      setResearchTaskId("");
      setResearchProgress(null);
    }
  };

  const runStrategyMatrix = async () => {
    if (!cfg) return;
    let retainErr = false;
    let trackingTaskId = "";
    try {
      const preset = STRATEGY_MATRIX_PRESETS[strategyPreset] || STRATEGY_MATRIX_PRESETS.balanced;
      let accepted: any = null;
      try {
        accepted = await apiPost<any>(
          "/auto-trader/research/strategy-matrix/run",
          {
            market: cfg.market,
            top_n: preset.top_n,
            max_strategies: preset.max_strategies,
            max_drawdown_limit_pct: preset.max_drawdown_limit_pct,
            min_symbols_used: preset.min_symbols_used,
            matrix_overrides: preset.matrix_overrides,
            async_run: true,
          },
          {
            timeoutMs: 15000,
            retries: 0,
          }
        );
      } catch (startErr: any) {
        if (!isTransientRequestError(startErr)) throw startErr;
        const recovered = await recoverAcceptedTask("strategy_matrix");
        if (!recovered) throw startErr;
        accepted = recovered;
        retainErr = true;
        setError("策略矩阵启动请求超时，已自动接管后台任务并继续跟踪进度。");
      }
      if (accepted && accepted.accepted === false && accepted.message === "duplicate_task_reused") {
        retainErr = true;
        setError("已复用进行中的相同参数矩阵任务，未启动新任务；结果文件可能仍是上一次完成的快照。");
      }
      trackingTaskId = String(accepted?.task_id || "").trim();
      if (trackingTaskId) {
        setStrategyMatrixRunning(true);
        setStrategyMatrixTaskId(trackingTaskId);
        setStrategyMatrixProgress({
          taskId: trackingTaskId,
          status: "queued",
          progressPct: 0,
          progressStage: "queued",
          progressText: "任务排队中",
          queuePosition: 0,
          queueAhead: 0,
        });
      }
      await loadResearch(true, { retainError: retainErr });
      if (!retainErr) setError("");
      if (!trackingTaskId) {
        setStrategyMatrixRunning(false);
        setStrategyMatrixTaskId("");
        setStrategyMatrixProgress(null);
      }
    } catch (e: any) {
      retainErr = true;
      const queueMsg = mapQueueBusyError(e, "策略矩阵");
      setError(queueMsg || `策略参数矩阵运行失败: ${String(e?.message || e)}`);
      setStrategyMatrixRunning(false);
      setStrategyMatrixTaskId("");
      setStrategyMatrixProgress(null);
      await loadResearch(true, { retainError: true });
    }
  };

  const cancelStrategyMatrix = async () => {
    const taskId = String(strategyMatrixTaskId || "").trim();
    if (!taskId) return;
    try {
      await apiPost<any>(`/auto-trader/research/tasks/${encodeURIComponent(taskId)}/cancel`, {});
      setError("已请求取消矩阵任务，稍后会停止。");
      setStrategyMatrixProgress((prev) =>
        prev ? { ...prev, status: "cancelled", progressStage: "cancelled", progressText: "任务已取消" } : prev
      );
    } catch (e: any) {
      setError(`取消矩阵任务失败: ${String(e?.message || e)}`);
    }
  };

  const runMlMatrix = async () => {
    if (!cfg) return;
    let retainErr = false;
    let trackingTaskId = "";
    try {
      let accepted: any = null;
      try {
        accepted = await apiPost<any>(
          "/auto-trader/research/ml-matrix/run",
          {
            market: cfg.market,
            kline: cfg.kline,
            top_n: Math.max(6, cfg.top_n || 8),
            signal_bars_days: Math.max(300, cfg.signal_bars_days || 300),
            async_run: true,
            matrix_overrides: {
              model_type_choices: ["random_forest", "gbdt", "logreg"],
              ml_threshold_choices: [0.5, 0.53, 0.56, 0.6],
              ml_horizon_days_choices: [3, 5, 8],
              ml_train_ratio_choices: [0.65, 0.7, 0.75],
              ml_walk_forward_windows_choices: [4, 6],
            },
            constraints: {
              min_oos_samples: 200,
              min_coverage: 0.05,
              min_precision: 0.45,
              min_accuracy: 0.52,
            },
          },
          {
            timeoutMs: 15000,
            retries: 0,
          }
        );
      } catch (startErr: any) {
        if (!isTransientRequestError(startErr)) throw startErr;
        const recovered = await recoverAcceptedTask("ml_matrix");
        if (!recovered) throw startErr;
        accepted = recovered;
        retainErr = true;
        setError("ML矩阵启动请求超时，已自动接管后台任务并继续跟踪进度。");
      }
      trackingTaskId = String(accepted?.task_id || "").trim();
      if (trackingTaskId) {
        setMlMatrixRunning(true);
        setMlMatrixTaskId(trackingTaskId);
        setMlMatrixProgress({
          taskId: trackingTaskId,
          status: "queued",
          progressPct: 0,
          progressStage: "queued",
          progressText: "任务排队中",
          queuePosition: 0,
          queueAhead: 0,
        });
      }
      await loadResearch(true, { retainError: retainErr });
      if (!retainErr) setError("");
      if (!trackingTaskId) {
        setMlMatrixRunning(false);
        setMlMatrixTaskId("");
        setMlMatrixProgress(null);
      }
    } catch (e: any) {
      retainErr = true;
      const queueMsg = mapQueueBusyError(e, "ML矩阵");
      setError(queueMsg || `ML矩阵运行失败: ${String(e?.message || e)}`);
      setMlMatrixRunning(false);
      setMlMatrixTaskId("");
      setMlMatrixProgress(null);
      await loadResearch(true, { retainError: true });
    }
  };

  const cancelMlMatrix = async () => {
    const taskId = String(mlMatrixTaskId || "").trim();
    if (!taskId) return;
    try {
      await apiPost<any>(`/auto-trader/research/tasks/${encodeURIComponent(taskId)}/cancel`, {});
      setError("已请求取消ML矩阵任务，稍后会停止。");
      setMlMatrixProgress((prev) =>
        prev ? { ...prev, status: "cancelled", progressStage: "cancelled", progressText: "任务已取消" } : prev
      );
    } catch (e: any) {
      setError(`取消ML矩阵任务失败: ${String(e?.message || e)}`);
    }
  };

  const canApplyMlMatrix =
    Boolean(mlMatrix?.ok) &&
    Boolean(
      (mlMatrix?.items && mlMatrix.items.length > 0) ||
        mlMatrix?.best_balanced ||
        mlMatrix?.best_high_precision ||
        mlMatrix?.best_high_coverage
    );

  const applyMlMatrixToConfig = async () => {
    if (!canApplyMlMatrix) return;
    if (
      !confirm(
        "将所选来源的 ML 参数写入自动交易配置，并默认开启 ML 过滤。\n若使用独立 Worker，请重启 Worker 后生效。\n是否继续？"
      )
    ) {
      return;
    }
    setMlApplyBusy(true);
    try {
      const res = await apiPost<any>("/auto-trader/research/ml-matrix/apply-to-config", {
        variant: mlApplyVariant,
        enable_ml_filter: true,
        snapshot_id: mlApplySnapshotId || undefined,
      });
      setError("");
      const src = String(res?.applied_from || "");
      const msg = String(res?.message || "已写入配置");
      window.alert(src ? `来源: ${src}\n${msg}` : msg);
    } catch (e: any) {
      let detail = String(e?.message || e);
      try {
        const j = JSON.parse(detail);
        const d = j?.detail;
        if (typeof d === "string") detail = d;
        else if (d && typeof d === "object") detail = JSON.stringify(d, null, 2);
      } catch {
        /* keep */
      }
      setError(`应用 ML 配置失败: ${detail}`);
    } finally {
      setMlApplyBusy(false);
    }
  };

  const exportResearchSnapshot = () => {
    const payload = researchSnapshot || { has_snapshot: false, snapshot: null };
    try {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `auto-trader-research-snapshot-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setError(`导出研究快照失败: ${String(e?.message || e)}`);
    }
  };

  const exportModelCompareCsv = () => {
    try {
      const rows = modelRows || [];
      if (!rows.length) {
        setError("暂无模型对比数据可导出，请先执行一次 Research。");
        return;
      }
      const header = ["模型名称", "运行次数", "平均分", "平均Acc", "最佳分"];
      const escapeCell = (v: unknown): string => {
        const s = String(v ?? "");
        if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
        return s;
      };
      const body = rows.map((x) =>
        [
          escapeCell(x.model_name ?? ""),
          escapeCell(x.runs ?? ""),
          escapeCell(x.avg_score ?? ""),
          escapeCell(x.avg_accuracy ?? ""),
          escapeCell(x.best_score ?? ""),
        ].join(",")
      );
      const csv = [header.join(","), ...body].join("\n");
      const blob = new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `auto-trader-model-compare-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (e: any) {
      setError(`导出模型对比CSV失败: ${String(e?.message || e)}`);
    }
  };

  const exportPairTradeCsv = () => {
    try {
      const pairKey = pairTradeFilterPair.trim().toUpperCase();
      const symbolKey = pairTradeFilterSymbol.trim().toUpperCase();
      const allTradeRows = buildPairTradeRows(selectedPairRows);
      const rows = allTradeRows.filter((r: any) => {
        const pairOk = !pairKey || String(r?.pair || "").toUpperCase().includes(pairKey);
        const symOk = !symbolKey || String(r?.symbol || "").toUpperCase().includes(symbolKey);
        return pairOk && symOk;
      });
      if (!rows.length) {
        setError("当前筛选条件下没有可导出的交易明细。");
        return;
      }
      const header = ["配对", "入选标的", "策略", "买入时间", "卖出时间", "买入价", "卖出价", "数量", "收益%", "收益额", "持有天数"];
      const escapeCell = (v: unknown): string => {
        const s = String(v ?? "");
        if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
        return s;
      };
      const body = rows.map((r: any) =>
        [
          escapeCell(r.pair),
          escapeCell(r.symbol),
          escapeCell(r.strategy),
          escapeCell(r.entry_date),
          escapeCell(r.exit_date),
          escapeCell(r.entry_price),
          escapeCell(r.exit_price),
          escapeCell(r.quantity),
          escapeCell(r.pnl_pct),
          escapeCell(r.pnl),
          escapeCell(r.hold_days),
        ].join(",")
      );
      const csv = [header.join(","), ...body].join("\n");
      const blob = new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `pair-trade-history-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (e: any) {
      setError(`导出组合交易明细CSV失败: ${String(e?.message || e)}`);
    }
  };

  const exportAbReportJson = () => {
    try {
      const payload = researchSnapshotData?.factor_ab_report || null;
      if (!payload) {
        setError("暂无 A/B 报告数据可导出，请先执行一次 Research。");
        return;
      }
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `auto-trader-ab-report-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (e: any) {
      setError(`导出A/B报告JSON失败: ${String(e?.message || e)}`);
    }
  };

  const exportAbReportMarkdown = () => {
    try {
      const text = String(abMarkdown || "").trim();
      if (!text) {
        setError("暂无 A/B 报告 Markdown 可导出，请先执行一次 Research。");
        return;
      }
      const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `auto-trader-ab-report-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.md`;
      a.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (e: any) {
      setError(`导出A/B报告Markdown失败: ${String(e?.message || e)}`);
    }
  };
  const researchSnapshotData = researchSnapshot?.snapshot;
  const allocationRows = researchSnapshotData?.allocation_plan || [];
  const modelRows = modelCompare?.items || [];
  const matrixRows = strategyMatrix?.items || [];
  const matrixBestBalanced = strategyMatrix?.best_balanced || null;
  const mlMatrixRows = mlMatrix?.items || [];
  const mlMatrixBestBalanced = mlMatrix?.best_balanced || null;
  const strategyRows = researchSnapshotData?.strategy_rankings || [];
  const matrixTopSymbolRows = useMemo(() => {
    if (!matrixRows.length) return [];
    const flat = matrixRows.flatMap((row) => {
      const tops = Array.isArray(row?.top_symbols) ? row.top_symbols : [];
      return tops.map((x) => ({
        symbol: String(x?.symbol || "").trim().toUpperCase(),
        net_return_pct: Number(x?.net_return_pct ?? Number.NEGATIVE_INFINITY),
        strategy: row?.strategy || "-",
        strategy_label: row?.strategy_label || row?.strategy || "-",
      }));
    });
    flat.sort((a, b) => Number(b.net_return_pct) - Number(a.net_return_pct));
    const picked: Array<{ symbol: string; net_return_pct: number; strategy: string; strategy_label: string }> = [];
    const seen = new Set<string>();
    for (const row of flat) {
      const sym = String(row.symbol || "");
      if (!sym || seen.has(sym)) continue;
      seen.add(sym);
      picked.push(row);
      if (picked.length >= 5) break;
    }
    return picked;
  }, [matrixRows]);
  const strategySummaryRows = useMemo(
    () =>
    matrixTopSymbolRows.length
      ? matrixTopSymbolRows.map((x) => ({
          symbol: x?.symbol || "-",
          best_strategy: {
            strategy: x?.strategy || "-",
            strategy_label: x?.strategy_label || x?.strategy || "-",
            composite_score: x?.net_return_pct,
          },
        }))
      : strategyRows,
    [matrixTopSymbolRows, strategyRows]
  );
  const pairBacktest = researchSnapshotData?.pair_backtest || null;
  const providerStatus = researchSnapshotData?.data_providers || {};
  const externalResearch = researchSnapshotData?.external_research || {};
  const regimeGating = researchSnapshotData?.regime_gating || {};
  const factorGating = researchSnapshotData?.factor_gating || {};
  const mlDiag = researchSnapshotData?.ml_diagnostics || {};
  const regimeInfo = externalResearch?.market_regime || {};
  const externalFactors = externalResearch?.symbol_factors || [];
  const abReport = researchSnapshotData?.factor_ab_report || null;
  const abSummary = abReport?.summary || {};
  const abItems = abReport?.items || [];
  const mlDiagModels = mlDiag?.models || [];
  const selectedPairRows = Array.isArray(pairBacktest?.selected_pairs) ? pairBacktest.selected_pairs : [];
  const pairPoolUsedFromSnapshot = researchSnapshotData?.pair_pool_used || [];
  const pairPoolUsed = useMemo(
    () =>
    pairPoolUsedFromSnapshot.length > 0
      ? pairPoolUsedFromSnapshot
      : Array.from(
          new Map(
            selectedPairRows
              .map((row: any) => {
                const longSym = String(row?.pair?.long || "").trim();
                const shortSym = String(row?.pair?.short || "").trim();
                if (!longSym || !shortSym) return null;
                return [`${longSym}=>${shortSym}`, { long_symbol: longSym, short_symbol: shortSym }] as const;
              })
              .filter(Boolean) as Array<readonly [string, { long_symbol: string; short_symbol: string }]>
          ).values()
        ),
    [pairPoolUsedFromSnapshot, selectedPairRows]
  );
  const pairTradeTotalCount = useMemo(
    () =>
      selectedPairRows.reduce((acc: number, row: any) => {
        const trades = Array.isArray(row?.selected_metrics?.trade_history) ? row.selected_metrics.trade_history : [];
        return acc + trades.length;
      }, 0),
    [selectedPairRows]
  );
  const pairTradeRows = useMemo(
    () => buildPairTradeRows(selectedPairRows, MAX_FILTER_SCAN_PAIR_TRADE_ROWS),
    [buildPairTradeRows, selectedPairRows]
  );
  const filteredPairTradeRows = useMemo(() => {
    const pairKey = pairTradeFilterPair.trim().toUpperCase();
    const symbolKey = pairTradeFilterSymbol.trim().toUpperCase();
    return pairTradeRows.filter((r: any) => {
      const pairOk = !pairKey || String(r?.pair || "").toUpperCase().includes(pairKey);
      const symOk = !symbolKey || String(r?.symbol || "").toUpperCase().includes(symbolKey);
      return pairOk && symOk;
    });
  }, [pairTradeRows, pairTradeFilterPair, pairTradeFilterSymbol]);
  const visibleAllocationRows = useMemo(() => allocationRows.slice(0, MAX_RENDER_ALLOC_ROWS), [allocationRows]);
  const visiblePairPoolRows = useMemo(() => pairPoolUsed.slice(0, MAX_RENDER_PAIR_POOL_ROWS), [pairPoolUsed]);
  const visibleSelectedPairRows = useMemo(
    () => selectedPairRows.slice(0, MAX_RENDER_SELECTED_PAIR_ROWS),
    [selectedPairRows]
  );
  const visibleMlDiagModels = useMemo(() => mlDiagModels.slice(0, MAX_RENDER_ML_DIAG_ROWS), [mlDiagModels]);
  const visibleFilteredPairTradeRows = useMemo(
    () => filteredPairTradeRows.slice(0, MAX_RENDER_PAIR_TRADE_ROWS),
    [filteredPairTradeRows]
  );
  const showResearchProgress = Boolean(researchRunning && (researchTaskId || researchProgress));
  const showStrategyProgress = Boolean(strategyMatrixRunning && (strategyMatrixTaskId || strategyMatrixProgress));
  const showMlProgress = Boolean(mlMatrixRunning && (mlMatrixTaskId || mlMatrixProgress));
  const renderTaskProgress = (x: TaskProgress | null, fallbackTaskId: string) => {
    if (!x && !fallbackTaskId) return null;
    const pct = Math.max(0, Math.min(100, Math.round(Number(x?.progressPct ?? 0))));
    const taskId = x?.taskId || fallbackTaskId;
    const text = x?.progressText || (x?.status === "queued" ? "任务排队中" : "任务运行中");
    const stage = String(x?.progressStage || x?.status || "running");
    const queueHint =
      stage === "queued" && Number(x?.queuePosition || 0) > 0
        ? ` · 队列第${Number(x?.queuePosition)}（前方${Number(x?.queueAhead || 0)}）`
        : "";
    return (
      <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
        <div className="mb-1 flex items-center justify-between">
          <span className="truncate pr-2">{text}</span>
          <span className="font-mono text-cyan-300">{pct}%</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded bg-slate-800">
          <div
            className="h-full rounded bg-gradient-to-r from-cyan-500 to-emerald-500 transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="mt-1 text-[11px] text-slate-500">
          {taskId ? `任务ID: ${taskId} · ` : ""}阶段: {stage}
          {queueHint}
        </div>
      </div>
    );
  };
  return (
    <div className="space-y-3">
      {error ? (
        <div className="panel border-rose-200 bg-rose-50 text-rose-700">
          <div className={SUB_TITLE_CLS}>错误信息</div>
          <div className="mt-1 text-sm">{error}</div>
        </div>
      ) : null}
      <div className="rounded-lg border border-indigo-500/30 bg-indigo-950/30 p-3 text-xs text-slate-300">
        研究任务参数取自{" "}
        <Link className="text-cyan-300 underline hover:text-cyan-200" href="/auto-trader">
          Auto Trader
        </Link>
        {" "}
        当前保存的配置（市场、K线、TopN、回测天数、信号天数等）。修改后请在 Auto Trader 页面保存配置。
      </div>
<div className="panel space-y-3">
  <div className="flex flex-wrap items-center justify-between gap-2">
    <div className={PANEL_TITLE_CLS}>研究中心（P0 / P1 / P2）</div>
    <div className="flex flex-wrap gap-2">
      <select
        className={`${INPUT_CLS} max-w-[220px]`}
        value={strategyPreset}
        onChange={(e) => setStrategyPreset(e.target.value as StrategyMatrixPresetKey)}
        title="策略矩阵筛选预设"
        disabled={strategyMatrixRunning || !cfg}
      >
        <option value="conservative">策略矩阵预设：保守（最快）</option>
        <option value="balanced">策略矩阵预设：平衡（推荐）</option>
        <option value="aggressive">策略矩阵预设：激进（更全面）</option>
      </select>
      <button
        className="rounded-lg bg-gradient-to-r from-indigo-600 to-violet-600 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
        onClick={runResearch}
        disabled={researchRunning || !cfg}
      >
        {researchRunning ? `Research 运行中${researchTaskId ? ` (${researchTaskId})` : "..."}` : "一键运行 Research"}
      </button>
      <button
        className="rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
        onClick={runStrategyMatrix}
        disabled={strategyMatrixRunning || !cfg}
      >
        {strategyMatrixRunning ? "矩阵筛选中..." : "策略参数矩阵筛选"}
      </button>
      <button
        className="rounded-lg bg-gradient-to-r from-sky-600 to-blue-700 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
        onClick={runMlMatrix}
        disabled={mlMatrixRunning || !cfg}
      >
        {mlMatrixRunning ? "ML矩阵运行中..." : "ML参数矩阵筛选"}
      </button>
    </div>
  </div>
  <div className="text-[11px] text-slate-400">
    当前矩阵预设：{STRATEGY_MATRIX_PRESETS[strategyPreset]?.label || "-"}（切换后再点“策略参数矩阵筛选”）
  </div>
  {showResearchProgress || showStrategyProgress || showMlProgress ? (
    <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
      {showResearchProgress ? renderTaskProgress(researchProgress, researchTaskId) : <div />}
      {showStrategyProgress ? renderTaskProgress(strategyMatrixProgress, strategyMatrixTaskId) : <div />}
      {showMlProgress ? renderTaskProgress(mlMatrixProgress, mlMatrixTaskId) : <div />}
    </div>
  ) : null}
  <div className="flex flex-wrap gap-2">
    <details className="rounded-lg border border-slate-700/70 bg-slate-900/40 px-3 py-2 text-xs text-slate-200">
      <summary className="cursor-pointer font-medium">任务控制</summary>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs font-medium text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
          onClick={cancelStrategyMatrix}
          disabled={!strategyMatrixRunning || !strategyMatrixTaskId}
        >
          取消矩阵任务
        </button>
        <button
          className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs font-medium text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
          onClick={cancelMlMatrix}
          disabled={!mlMatrixRunning || !mlMatrixTaskId}
        >
          取消ML矩阵任务
        </button>
      </div>
    </details>
    <details className="rounded-lg border border-slate-700/70 bg-slate-900/40 px-3 py-2 text-xs text-slate-200">
      <summary className="cursor-pointer font-medium">导出工具</summary>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          className="rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-2 text-xs font-medium text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50"
          onClick={exportResearchSnapshot}
          disabled={!researchSnapshotData}
        >
          导出研究快照 JSON
        </button>
        <button
          className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs font-medium text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-50"
          onClick={exportModelCompareCsv}
          disabled={!modelRows.length}
        >
          导出模型对比 CSV
        </button>
        <button
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-200 hover:bg-amber-500/20 disabled:opacity-50"
          onClick={exportPairTradeCsv}
          disabled={!pairTradeRows.length}
        >
          导出组合交易明细 CSV
        </button>
        <button
          className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-2 text-xs font-medium text-violet-200 hover:bg-violet-500/20 disabled:opacity-50"
          onClick={exportAbReportJson}
          disabled={!abReport}
        >
          导出 A/B 报告 JSON
        </button>
        <button
          className="rounded-lg border border-fuchsia-500/40 bg-fuchsia-500/10 px-3 py-2 text-xs font-medium text-fuchsia-200 hover:bg-fuchsia-500/20 disabled:opacity-50"
          onClick={exportAbReportMarkdown}
          disabled={!abMarkdown.trim()}
        >
          导出 A/B 报告 Markdown
        </button>
      </div>
    </details>
  </div>
  <details className="rounded-lg border border-slate-700/70 bg-slate-900/40 p-3" open>
    <summary className="cursor-pointer text-sm font-medium text-slate-200">研究状态卡</summary>
    <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-6">
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>快照状态</div>
      <div className={`mt-1 text-sm font-semibold ${researchStatus?.has_snapshot ? "text-emerald-300" : "text-slate-300"}`}>
        {researchStatus?.has_snapshot ? "已生成" : "未生成"}
      </div>
    </div>
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>最近生成</div>
      <div className="mt-1 text-sm text-slate-200">{formatTime(researchStatus?.generated_at || undefined)}</div>
    </div>
    <div
      className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3"
      title="与 Auto Trader 当前保存配置一致；下次运行 Research 将使用此项"
    >
      <div className={SUB_TITLE_CLS}>市场/K线（配置）</div>
      <div className="mt-1 text-sm text-cyan-300">
        {(cfg?.market ?? researchStatus?.market ?? "-").toString().toUpperCase()} /{" "}
        {(cfg?.kline ?? researchStatus?.kline ?? "-").toString().toUpperCase()}
      </div>
    </div>
    <div
      className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3"
      title="与 Auto Trader 当前保存配置一致"
    >
      <div className={SUB_TITLE_CLS}>TopN（配置）</div>
      <div className="mt-1 text-sm text-slate-200">{cfg?.top_n ?? researchStatus?.top_n ?? "-"}</div>
    </div>
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>版本号</div>
      <div className="mt-1 truncate text-xs text-slate-300" title={researchStatus?.version || "-"}>
        {researchStatus?.version || "-"}
      </div>
    </div>
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>任务队列</div>
      <div className="mt-1 text-sm text-amber-300">
        {researchStatus?.task_queue?.active ?? 0} / {researchStatus?.task_queue?.max_pending ?? "-"}
      </div>
      <div className="mt-1 text-[11px] text-slate-400">
        运行 {researchStatus?.task_queue?.running ?? 0} · 排队 {researchStatus?.task_queue?.queued ?? 0}
      </div>
    </div>
    </div>
    <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-5">
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        数据源：{providerStatus?.primary || "longport"} · OpenBB{" "}
        {providerStatus?.openbb_enabled ? (providerStatus?.openbb_connected ? "已连接" : "未连接") : "未启用"}
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>外部市场状态：{regimeInfo?.available ? regimeInfo?.regime || "unknown" : "-"}</div>
        <div className="mt-1 text-slate-400">
          置信度：{typeof regimeInfo?.confidence === "number" ? regimeInfo.confidence : "-"} | 基准：
          {regimeInfo?.symbol || "-"}
        </div>
        <div className="mt-1 text-slate-400">
          特征：ret20={typeof regimeInfo?.features?.ret_20 === "number" ? regimeInfo.features.ret_20 : "-"} | vol_z=
          {typeof regimeInfo?.features?.vol_z === "number" ? regimeInfo.features.vol_z : "-"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>外部因子样本：{externalFactors.length}</div>
        <div className="mt-1 text-slate-400">
          因子应用：{factorGating?.applied ? "已应用" : "未应用"} ·
          {typeof factorGating?.available_symbols === "number" ? ` ${factorGating.available_symbols}` : " -"} /
          {typeof factorGating?.total_symbols === "number" ? ` ${factorGating.total_symbols}` : " -"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>Regime风控：{regimeGating?.applied ? "已应用" : "未应用"}</div>
        <div className="mt-1 text-slate-400">
          仓位上限：{typeof regimeGating?.effective_exposure === "number" ? `${(regimeGating.effective_exposure * 100).toFixed(1)}%` : "-"}
        </div>
        <div className="mt-1 text-slate-400">
          单标的上限：
          {typeof regimeGating?.max_single_ratio === "number" ? `${(regimeGating.max_single_ratio * 100).toFixed(1)}%` : "-"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>ML诊断：{mlDiag?.enabled ? "已生成" : "未生成"}</div>
        <div className="mt-1 text-slate-400">
          样本：{mlDiag?.dataset?.samples ?? "-"} | 标签正样本率：
          {typeof mlDiag?.label_distribution?.positive_ratio === "number"
            ? `${(mlDiag.label_distribution.positive_ratio * 100).toFixed(1)}%`
            : "-"}
        </div>
        <div className="mt-1 text-slate-400">
          成本标签(bps)：{mlDiag?.settings?.transaction_cost_bps ?? "-"} | 预测周期：{mlDiag?.settings?.horizon_days ?? "-"}天
        </div>
      </div>
    </div>
  </details>

  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
    <details
      className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
      onToggle={onResearchPanelToggle(setModelComparePanelOpen, "model_compare")}
    >
      <summary className="cursor-pointer text-sm font-medium text-slate-200">模型对比榜（P2）</summary>
      {modelComparePanelOpen && sectionLoading.model_compare ? (
        <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
      ) : null}
      {modelComparePanelOpen && !sectionLoading.model_compare && !modelRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无模型对比数据，先执行一次 Research。</div>
      ) : modelComparePanelOpen && !sectionLoading.model_compare ? (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[520px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">模型</th>
                <th className="py-1">运行次数</th>
                <th className="py-1">平均分</th>
                <th className="py-1">平均Acc</th>
                <th className="py-1">最佳分</th>
              </tr>
            </thead>
            <tbody>
              {modelRows.slice(0, 10).map((x, idx) => (
                <tr key={`${x.model_name || "model"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{x.model_name || "-"}</td>
                  <td className="py-1">{x.runs ?? "-"}</td>
                  <td className="py-1 text-cyan-300">{x.avg_score ?? "-"}</td>
                  <td className="py-1 text-sky-300">{x.avg_accuracy ?? "-"}</td>
                  <td className="py-1 text-emerald-300">{x.best_score ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </details>

    <details
      className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
      onToggle={onResearchPanelToggle(setStrategySummaryPanelOpen, "strategy_summary")}
    >
      <summary className="cursor-pointer text-sm font-medium text-slate-200">
        策略优选摘要（P1）{matrixTopSymbolRows.length ? " · 已同步矩阵全组合Top5" : ""}
      </summary>
      {strategySummaryPanelOpen && sectionLoading.strategy_summary ? (
        <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
      ) : null}
      {strategySummaryPanelOpen && !sectionLoading.strategy_summary && !strategySummaryRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无策略摘要数据，先执行一次 Research。</div>
      ) : strategySummaryPanelOpen && !sectionLoading.strategy_summary ? (
        <div className="mt-2 space-y-1">
          {strategySummaryRows.slice(0, 5).map((row, idx) => {
            const best = row?.best_strategy || {};
            return (
              <div
                key={`${row.symbol || "symbol"}-${idx}`}
                className="rounded border border-slate-800/90 bg-slate-950/40 px-2 py-1 text-xs text-slate-300"
              >
                <span className="text-cyan-300">{row.symbol || "-"}</span>
                {" | "}
                <span>{best.strategy_label || best.strategy || "-"}</span>
                {" | "}
                <span className="text-emerald-300">score: {best.composite_score ?? "-"}</span>
              </div>
            );
          })}
        </div>
      ) : null}
    </details>
  </div>

  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setStrategyMatrixPanelOpen, "strategy_matrix")}
  >
    <summary className="cursor-pointer text-sm font-medium text-slate-200">策略参数矩阵（优秀策略筛选）</summary>
    {strategyMatrixPanelOpen && sectionLoading.strategy_matrix ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {strategyMatrixPanelOpen && strategyMatrixRunning ? (
      <div className="mt-2">{renderTaskProgress(strategyMatrixProgress, strategyMatrixTaskId)}</div>
    ) : null}
    {strategyMatrixPanelOpen && strategyMatrix?.generated_at ? (
      <div className="mt-2 text-[11px] text-slate-500">
        结果快照：<span className="text-slate-400">{strategyMatrix.generated_at}</span>
        {strategyMatrix.trace_id ? (
          <>
            {" "}
            · trace <span className="font-mono text-slate-400">{strategyMatrix.trace_id}</span>
          </>
        ) : null}
        {typeof strategyMatrix.candidate_count === "number" ? (
          <>
            {" "}
            · 过滤前候选 <span className="text-cyan-600/90">{strategyMatrix.candidate_count}</span> / 表格行{" "}
            <span className="text-cyan-600/90">{matrixRows.length}</span>
          </>
        ) : null}
      </div>
    ) : null}
    {strategyMatrixPanelOpen && !sectionLoading.strategy_matrix && !matrixRows.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无矩阵结果，点击“策略参数矩阵筛选”开始。</div>
    ) : strategyMatrixPanelOpen && !sectionLoading.strategy_matrix ? (
      <div className="mt-2 space-y-3">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            结果条数：<span className="text-cyan-300">{matrixRows.length}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            组合网格：<span className="text-cyan-300">{strategyMatrix?.grid_size ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            策略数：<span className="text-cyan-300">{strategyMatrix?.strategy_count ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            推荐（平衡）：
            <span className="text-emerald-300">{matrixBestBalanced?.strategy || "-"}</span>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">策略</th>
                <th className="py-1">K线</th>
                <th className="py-1">回测天数</th>
                <th className="py-1">成本(bps)</th>
                <th className="py-1">净收益%</th>
                <th className="py-1">回撤%</th>
                <th className="py-1">Sharpe</th>
                <th className="py-1">胜率%</th>
                <th className="py-1">样本</th>
                <th className="py-1">矩阵分</th>
              </tr>
            </thead>
            <tbody>
              {matrixRows.slice(0, 20).map((x, idx) => (
                <tr key={`${x.strategy || "s"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">
                    <div>{x.strategy_label || x.strategy || "-"}</div>
                    {x.strategy_params && Object.keys(x.strategy_params).length ? (
                      <div className="text-[11px] text-slate-400">{JSON.stringify(x.strategy_params)}</div>
                    ) : null}
                  </td>
                  <td className="py-1">{x.kline || "-"}</td>
                  <td className="py-1">{x.backtest_days ?? "-"}</td>
                  <td className="py-1">
                    {(x.commission_bps ?? "-")}/{(x.slippage_bps ?? "-")}
                  </td>
                  <td className="py-1 text-emerald-300">{x.avg_net_return_pct ?? "-"}</td>
                  <td className="py-1 text-amber-300">{x.avg_max_drawdown_pct ?? "-"}</td>
                  <td className="py-1">{x.avg_sharpe_ratio ?? "-"}</td>
                  <td className="py-1">{x.avg_win_rate_pct ?? "-"}</td>
                  <td className="py-1">
                    {x.symbols_used ?? "-"}/{x.symbols_total ?? "-"}
                  </td>
                  <td className="py-1 text-cyan-300">{x.matrix_score ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ) : null}
  </details>

  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setMlMatrixPanelOpen, "ml_matrix")}
  >
    <summary className="cursor-pointer text-sm font-medium text-slate-200">ML参数矩阵（可信参数筛选）</summary>
    {mlMatrixPanelOpen && sectionLoading.ml_matrix ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {mlMatrixPanelOpen && mlMatrixRunning ? (
      <div className="mt-2">{renderTaskProgress(mlMatrixProgress, mlMatrixTaskId)}</div>
    ) : null}
    {mlMatrixPanelOpen && !sectionLoading.ml_matrix && !mlMatrixRows.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无ML矩阵结果，点击“ML参数矩阵筛选”开始。</div>
    ) : mlMatrixPanelOpen && !sectionLoading.ml_matrix ? (
      <div className="mt-2 space-y-3">
        {mlMatrix?.signal_bars_days_note ? (
          <div className="rounded border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-200">
            {mlMatrix.signal_bars_days_note}
          </div>
        ) : null}
        {Array.isArray(mlMatrix?.bar_fetch_preflight) && mlMatrix!.bar_fetch_preflight!.length ? (
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            <div className="mb-1 font-medium text-slate-200">K 线预检（raw=接口返回根数，feature=净特征行）</div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {mlMatrix!.bar_fetch_preflight!.map((p) => (
                <span key={String(p.symbol)}>
                  <span className="text-cyan-300">{p.symbol || "-"}</span>: raw {p.raw_bars ?? "-"} / feat{" "}
                  {p.feature_rows ?? "-"}{p.meets_matrix_min ? "" : " ⚠"}
                  {p.error ? <span className="text-rose-400"> {p.error}</span> : null}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            结果条数：<span className="text-cyan-300">{mlMatrixRows.length}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            组合网格：<span className="text-cyan-300">{mlMatrix?.grid_size ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            通过约束：<span className="text-cyan-300">{mlMatrix?.passed_constraints_count ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            推荐（平衡）：
            <span className="text-emerald-300">{mlMatrixBestBalanced?.params?.model_type || "-"}</span>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1120px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">模型</th>
                <th className="py-1">阈值</th>
                <th className="py-1">Horizon</th>
                <th className="py-1">TrainRatio</th>
                <th className="py-1">WF窗口</th>
                <th className="py-1">Acc</th>
                <th className="py-1">Precision</th>
                <th className="py-1">Recall</th>
                <th className="py-1">Coverage</th>
                <th className="py-1">OOS</th>
                <th className="py-1">通过</th>
                <th className="py-1">评分</th>
              </tr>
            </thead>
            <tbody>
              {mlMatrixRows.slice(0, 20).map((x, idx) => (
                <tr key={`${x?.params?.model_type || "mm"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{x?.params?.model_type || "-"}</td>
                  <td className="py-1">{x?.params?.ml_threshold ?? "-"}</td>
                  <td className="py-1">{x?.params?.ml_horizon_days ?? "-"}</td>
                  <td className="py-1">{x?.params?.ml_train_ratio ?? "-"}</td>
                  <td className="py-1">{x?.params?.ml_walk_forward_windows ?? "-"}</td>
                  <td className="py-1">{x?.metrics?.accuracy ?? "-"}</td>
                  <td className="py-1 text-cyan-300">{x?.metrics?.precision ?? "-"}</td>
                  <td className="py-1">{x?.metrics?.recall ?? "-"}</td>
                  <td className="py-1 text-emerald-300">{x?.metrics?.coverage ?? "-"}</td>
                  <td className="py-1">{x?.metrics?.oos_samples ?? "-"}</td>
                  <td className="py-1">{x?.pass_constraints ? "是" : "否"}</td>
                  <td className="py-1 text-sky-300">{x?.score ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ) : null}
  </details>

  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setMlDiagPanelOpen, "ml_diag")}
  >
    <summary className="cursor-pointer text-sm font-medium text-slate-200">ML诊断详情（Walk-forward）</summary>
    {mlDiagPanelOpen && sectionLoading.ml_diag ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {mlDiagPanelOpen && !sectionLoading.ml_diag && !mlDiagModels.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无 ML 诊断详情，先执行一次 Research。</div>
    ) : mlDiagPanelOpen && !sectionLoading.ml_diag ? (
      <div className="mt-2 space-y-2">
        <div className="text-xs text-slate-400">
          样本 {mlDiag?.dataset?.samples ?? "-"} · 使用标的 {mlDiag?.dataset?.symbols_used ?? "-"} /
          {mlDiag?.dataset?.symbols_requested ?? "-"} · 特征 {mlDiag?.settings?.feature_count ?? "-"} 个
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">模型</th>
                <th className="py-1">最新UpProb</th>
                <th className="py-1">Acc</th>
                <th className="py-1">Precision</th>
                <th className="py-1">Recall</th>
                <th className="py-1">Coverage</th>
                <th className="py-1">窗口数</th>
                <th className="py-1">OOS样本</th>
                <th className="py-1">WF覆盖条数</th>
              </tr>
            </thead>
            <tbody>
              {visibleMlDiagModels.map((x, idx) => {
                const wf = x?.walk_forward || {};
                return (
                  <tr key={`${x.model_name || "ml"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                    <td className="py-1">{x.model_name || "-"}</td>
                    <td className="py-1 text-cyan-300">{x.latest_up_probability ?? "-"}</td>
                    <td className="py-1">{wf.accuracy ?? "-"}</td>
                    <td className="py-1">{wf.precision ?? "-"}</td>
                    <td className="py-1">{wf.recall ?? "-"}</td>
                    <td className="py-1">{wf.coverage ?? "-"}</td>
                    <td className="py-1">{wf.windows ?? "-"}</td>
                    <td className="py-1">{wf.oos_samples ?? "-"}</td>
                    <td className="py-1">{x.walk_forward_coverage ?? "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    ) : null}
  </details>

  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setAbPanelOpen, "ab_report")}
  >
    <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-200">
      <span>A/B 报告（Baseline vs WithFactor）</span>
      <span
        className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] ${
          Number(factorGating?.available_symbols || 0) > 0
            ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
            : "bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30"
        }`}
        title={
          Number(factorGating?.available_symbols || 0) > 0
            ? "已检测到可用外部因子样本"
            : "当前外部因子样本不可用，报告仍可用于结构对比"
        }
      >
        {Number(factorGating?.available_symbols || 0) > 0 ? "因子数据可用" : "因子数据不足"}
      </span>
    </summary>
    {abPanelOpen && sectionLoading.ab_report ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {abPanelOpen && !sectionLoading.ab_report && !abReport ? (
      <div className="mt-2 text-xs text-slate-400">暂无 A/B 报告，先执行一次 Research。</div>
    ) : abPanelOpen && !sectionLoading.ab_report ? (
      <div className="mt-2 space-y-3">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            Top5重合：<span className="text-cyan-300">{abSummary?.overlap_count ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            平均最佳分Δ：<span className="text-emerald-300">{abSummary?.avg_best_score_delta ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            分配换手：<span className="text-amber-300">{abSummary?.allocation_turnover ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            生成时间：<span className="text-slate-200">{formatTime(abReport?.generated_at)}</span>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-2 text-xs text-slate-300 md:grid-cols-2">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2">
            Baseline Top5：{(abSummary?.top5_baseline || []).join(", ") || "-"}
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2">
            WithFactor Top5：{(abSummary?.top5_with_factor || []).join(", ") || "-"}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead className="bg-slate-900/60 text-left text-slate-300">
              <tr>
                <th className="px-3 py-2">标的</th>
                <th className="px-3 py-2">Score(B)</th>
                <th className="px-3 py-2">Score(F)</th>
                <th className="px-3 py-2">ΔScore</th>
                <th className="px-3 py-2">Multiplier</th>
                <th className="px-3 py-2">W(B)</th>
                <th className="px-3 py-2">W(F)</th>
                <th className="px-3 py-2">ΔW</th>
              </tr>
            </thead>
            <tbody>
              {abItems.slice(0, 10).map((x, idx) => (
                <tr key={`${x.symbol || "ab"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="px-3 py-2">{x.symbol || "-"}</td>
                  <td className="px-3 py-2">{x.score_baseline ?? "-"}</td>
                  <td className="px-3 py-2">{x.score_with_factor ?? "-"}</td>
                  <td className="px-3 py-2">{x.score_delta ?? "-"}</td>
                  <td className="px-3 py-2">{x.factor_multiplier ?? "-"}</td>
                  <td className="px-3 py-2">{x.weight_baseline ?? "-"}</td>
                  <td className="px-3 py-2">{x.weight_with_factor ?? "-"}</td>
                  <td className="px-3 py-2">{x.weight_delta ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ) : null}
  </details>

  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setAllocationPanelOpen, "allocation")}
  >
    <summary className="cursor-pointer text-sm font-medium text-slate-200">分配计划表（P1）</summary>
    {allocationPanelOpen && sectionLoading.allocation ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {allocationPanelOpen && !sectionLoading.allocation && !allocationRows.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无分配计划，先执行一次 Research。</div>
    ) : allocationPanelOpen && !sectionLoading.allocation ? (
      <>
        {allocationRows.length > visibleAllocationRows.length ? (
          <div className="mt-2 text-[11px] text-amber-300">
            为保证页面流畅，仅展示前 {visibleAllocationRows.length} 条（共 {allocationRows.length} 条）。
          </div>
        ) : null}
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="bg-slate-900/60 text-left text-slate-300">
              <tr>
                <th className="px-3 py-2">代码</th>
                <th className="px-3 py-2">原始权重</th>
                <th className="px-3 py-2">建议权重</th>
                <th className="px-3 py-2">Δ权重</th>
                <th className="px-3 py-2">强度分数</th>
                <th className="px-3 py-2">价格类型</th>
              </tr>
            </thead>
            <tbody>
              {visibleAllocationRows.map((x, idx) => (
                <tr key={`${x.symbol || "alloc"}-${idx}`} className="border-t border-slate-800/90">
                  <td className="px-3 py-2 font-medium text-slate-100">{x.symbol || "-"}</td>
                  <td className="px-3 py-2 text-slate-300">
                    {typeof x.weight_raw === "number" ? `${(x.weight_raw * 100).toFixed(2)}%` : "-"}
                  </td>
                  <td className="px-3 py-2 text-cyan-300">
                    {typeof x.weight === "number" ? `${(x.weight * 100).toFixed(2)}%` : "-"}
                  </td>
                  <td
                    className={`px-3 py-2 ${
                      typeof x.weight === "number" && typeof x.weight_raw === "number"
                        ? x.weight - x.weight_raw >= 0
                          ? "text-emerald-300"
                          : "text-amber-300"
                        : "text-slate-400"
                    }`}
                  >
                    {typeof x.weight === "number" && typeof x.weight_raw === "number"
                      ? `${((x.weight - x.weight_raw) * 100).toFixed(2)}%`
                      : "-"}
                  </td>
                  <td className="px-3 py-2">{x.strength_score ?? "-"}</td>
                  <td className="px-3 py-2 text-xs text-slate-300">{x.price_type || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    ) : null}
  </details>

  <details
    className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3"
    onToggle={onResearchPanelToggle(setPairBacktestPanelOpen, "pair_backtest")}
  >
    <summary className="cursor-pointer text-sm font-medium text-slate-200">ETF配对回测（只读快照）</summary>
    {pairBacktestPanelOpen && sectionLoading.pair_backtest ? (
      <div className="mt-2 text-xs text-cyan-300/90">正在加载…</div>
    ) : null}
    {pairBacktestPanelOpen && !sectionLoading.pair_backtest && !pairBacktest ? (
      <div className="mt-2 text-xs text-slate-400">暂无快照回测结果，请先执行一次 Research。</div>
    ) : pairBacktestPanelOpen && !sectionLoading.pair_backtest && pairBacktest?.error ? (
      <div className="mt-2 text-xs text-rose-300">回测快照错误：{String(pairBacktest.error)}</div>
    ) : pairBacktestPanelOpen && !sectionLoading.pair_backtest ? (
      <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-5">
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">市场：{pairBacktest?.market ?? "-"}</div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">K线：{pairBacktest?.kline ?? "-"}</div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm text-emerald-300">
          总收益估算：{pairBacktest?.portfolio_estimate?.total_return_pct ?? "-"}%
        </div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm text-amber-300">
          平均回撤估算：{pairBacktest?.portfolio_estimate?.avg_selected_max_drawdown_pct ?? "-"}%
        </div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">
          入选配对数：{(pairBacktest?.selected_pairs || []).length}
        </div>
      </div>
    ) : null}
    {pairBacktestPanelOpen && !sectionLoading.pair_backtest ? (
      <div className="mt-3 rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
      <div className="text-xs text-slate-400">
        配对池配置：{pairPoolUsed.length} 组
      </div>
      {!pairPoolUsed.length ? (
        <div className="mt-2 text-xs text-slate-400">当前市场未配置 ETF 配对池。</div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[520px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">多头ETF</th>
                <th className="py-1">反向ETF</th>
              </tr>
            </thead>
            <tbody>
              {visiblePairPoolRows.map((row, idx) => (
                <tr key={`${row.long_symbol || "long"}-${row.short_symbol || "short"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{row.long_symbol || "-"}</td>
                  <td className="py-1">{row.short_symbol || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pairPoolUsed.length > visiblePairPoolRows.length ? (
        <div className="mt-2 text-[11px] text-amber-300">
          为保证页面流畅，仅展示前 {visiblePairPoolRows.length} 组（共 {pairPoolUsed.length} 组）。
        </div>
      ) : null}
      </div>
    ) : null}
    {pairBacktestPanelOpen && !sectionLoading.pair_backtest ? (
      <div className="mt-3 rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
      <div className="text-xs text-slate-400">
        本次回测入选组合：{selectedPairRows.length} 组
      </div>
      {!selectedPairRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无入选组合（可能是配对池为空或全部评分未通过）。</div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          {selectedPairRows.length > visibleSelectedPairRows.length ? (
            <div className="mb-2 text-[11px] text-amber-300">
              为保证页面流畅，仅展示前 {visibleSelectedPairRows.length} 组（共 {selectedPairRows.length} 组）。
            </div>
          ) : null}
          <table className="w-full min-w-[760px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">配对</th>
                <th className="py-1">入选标的</th>
                <th className="py-1">入选策略</th>
                <th className="py-1">综合分</th>
                <th className="py-1">收益%</th>
                <th className="py-1">回撤%</th>
              </tr>
            </thead>
            <tbody>
              {visibleSelectedPairRows.map((row: any, idx: number) => {
                const pair = row?.pair || {};
                const metrics = row?.selected_metrics || {};
                return (
                  <tr key={`${pair?.long || "long"}-${pair?.short || "short"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                    <td className="py-1">{`${pair?.long || "-"} / ${pair?.short || "-"}`}</td>
                    <td className="py-1 text-cyan-300">{row?.selected_symbol || "-"}</td>
                    <td className="py-1">{metrics?.strategy_label || row?.selected_strategy || "-"}</td>
                    <td className="py-1">{row?.selected_score ?? "-"}</td>
                    <td className="py-1 text-emerald-300">{metrics?.total_return_pct ?? "-"}</td>
                    <td className="py-1 text-amber-300">{metrics?.max_drawdown_pct ?? "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      </div>
    ) : null}
    {pairBacktestPanelOpen && !sectionLoading.pair_backtest ? (
      <details className="mt-3 rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
      <summary className="cursor-pointer text-xs text-slate-300">
        组合交易明细（买入/卖出时间）：{pairTradeTotalCount} 笔（默认折叠）
      </summary>
      <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
        <input
          className={INPUT_CLS}
          placeholder="按组合筛选（示例：SPY.US / SH.US）"
          value={pairTradeFilterPair}
          onChange={(e) => setPairTradeFilterPair(e.target.value)}
        />
        <input
          className={INPUT_CLS}
          placeholder="按标的筛选（示例：SPY.US）"
          value={pairTradeFilterSymbol}
          onChange={(e) => setPairTradeFilterSymbol(e.target.value)}
        />
      </div>
      {!filteredPairTradeRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无交易明细（可能当前组合无成交或快照版本较旧）。</div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          {filteredPairTradeRows.length > visibleFilteredPairTradeRows.length ? (
            <div className="mb-2 text-[11px] text-amber-300">
              为保证页面流畅，仅展示前 {visibleFilteredPairTradeRows.length} 笔（筛选后共 {filteredPairTradeRows.length} 笔）。
            </div>
          ) : null}
          {pairTradeTotalCount > pairTradeRows.length ? (
            <div className="mb-2 text-[11px] text-slate-400">
              当前仅在前端扫描前 {pairTradeRows.length} 笔用于实时筛选；如需全量请使用“导出组合交易明细 CSV”。
            </div>
          ) : null}
          <table className="w-full min-w-[1080px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">配对</th>
                <th className="py-1">入选标的</th>
                <th className="py-1">策略</th>
                <th className="py-1">买入时间</th>
                <th className="py-1">卖出时间</th>
                <th className="py-1">买入价</th>
                <th className="py-1">卖出价</th>
                <th className="py-1">数量</th>
                <th className="py-1">收益%</th>
                <th className="py-1">收益额</th>
                <th className="py-1">持有天数</th>
              </tr>
            </thead>
            <tbody>
              {visibleFilteredPairTradeRows.map((r: any) => (
                <tr key={r.id} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{r.pair}</td>
                  <td className="py-1 text-cyan-300">{r.symbol}</td>
                  <td className="py-1">{r.strategy}</td>
                  <td className="py-1">{r.entry_date || "-"}</td>
                  <td className="py-1">{r.exit_date || "-"}</td>
                  <td className="py-1">{r.entry_price ?? "-"}</td>
                  <td className="py-1">{r.exit_price ?? "-"}</td>
                  <td className="py-1">{r.quantity ?? "-"}</td>
                  <td className={`py-1 ${Number(r.pnl_pct) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                    {r.pnl_pct ?? "-"}
                  </td>
                  <td className={`py-1 ${Number(r.pnl) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                    {r.pnl ?? "-"}
                  </td>
                  <td className="py-1">{r.hold_days ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </details>
    ) : null}
  </details>
</div>

    </div>
  );
}
