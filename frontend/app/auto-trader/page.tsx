"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { FALLBACK_STRATEGY_CATALOG, type StrategyCatalogItem } from "@/lib/backtest-strategy-catalog";
import { PageShell } from "@/components/ui/page-shell";
import { buildSwrOptions, SWR_INTERVALS } from "@/lib/swr-config";
import useSWR from "swr";

type AutoConfig = {
  enabled: boolean;
  auto_execute: boolean;
  pair_mode_allow_auto_execute?: boolean;
  dry_run_mode?: boolean;
  active_template?: string;
  signal_relaxed_mode?: boolean;
  auto_prune_invalid_symbols?: boolean;
  observer_mode_enabled?: boolean;
  observer_no_signal_rounds?: number;
  auto_sell_enabled?: boolean;
  sell_full_position?: boolean;
  sell_order_quantity?: number;
  same_symbol_max_sells_per_day?: number;
  same_symbol_cooldown_minutes?: number;
  same_symbol_max_trades_per_day?: number;
  avoid_add_to_existing_position?: boolean;
  market: "us" | "hk" | "cn";
  pair_mode: boolean;
  interval_seconds: number;
  top_n: number;
  kline: "1m" | "5m" | "10m" | "30m" | "1h" | "2h" | "4h" | "1d";
  backtest_days: number;
  signal_bars_days: number;
  order_quantity: number;
  entry_rule?: "strategy_cross" | "breakout" | "mean_reversion";
  breakout_lookback_bars?: number;
  breakout_volume_ratio?: number;
  mean_reversion_rsi_threshold?: number;
  mean_reversion_deviation_pct?: number;
  exit_rules?: string[];
  rule_priority?: string[];
  hard_stop_pct?: number;
  take_profit_pct?: number;
  time_stop_hours?: number;
  sizer?: {
    type?: "fixed" | "risk_percent" | "volatility";
    quantity?: number;
    risk_pct?: number;
    target_vol_pct?: number;
  };
  cost_model?: {
    commission_bps?: number;
    slippage_bps?: number;
  };
  max_daily_trades: number;
  max_position_value: number;
  max_total_exposure: number;
  min_cash_ratio: number;
  same_direction_max_new_orders_per_scan?: number;
  max_concurrent_long_positions?: number;
  ml_filter_enabled?: boolean;
  ml_model_type?: "logreg" | "random_forest" | "gbdt";
  ml_threshold?: number;
  ml_horizon_days?: number;
  ml_train_ratio?: number;
  ml_walk_forward_windows?: number;
  ml_filter_cache_minutes?: number;
  research_allocation_enabled?: boolean;
  research_allocation_max_age_minutes?: number;
  research_allocation_snapshot_id?: string;
  research_allocation_notional_scale?: number;
  /** 将策略参数矩阵结果中 matrix_score 前 3 名变体（含 strategy_params）并入扫描评分 */
  merge_strategy_matrix_top3?: boolean;
  merge_strategy_matrix_top3_snapshot_id?: string;
  strategies: string[];
  /** 各策略参与评分/回测时使用的参数（与后端 cfg.strategy_params_map 一致） */
  strategy_params_map?: Record<string, Record<string, unknown>>;
  pair_pool: {
    us: Record<string, string>;
    hk: Record<string, string>;
    cn: Record<string, string>;
  };
  universe: {
    us: string[];
    hk: string[];
    cn: string[];
  };
};

type SignalItem = {
  signal_id: string;
  status: string;
  symbol: string;
  action: string;
  quantity: number;
  suggested_price?: number;
  strategy?: string;
  strategy_label?: string;
  strategy_params?: Record<string, unknown>;
  strategy_score?: number;
  created_at: string;
  expires_at: string;
  auto_executed?: boolean;
  execution_result?: any;
  reason?: string;
  trace?: any;
};

type TemplateItem = {
  name: "trend" | "mean_reversion" | "defensive";
  label: string;
  description?: string;
  config?: Partial<AutoConfig>;
};

type ConfigBackupItem = {
  id: string;
  created_at: string;
  reason?: string;
  active_template?: string;
  market?: string;
  kline?: string;
};

type StrategyOption = {
  name: string;
  label?: string;
  description?: string;
  category?: string;
  risk_level?: string;
  default_params?: Record<string, any>;
};

type ScanDiagnostics = {
  scan_time?: string | null;
  /** Worker 最近一次完整扫描写入 last_scan_summary 的时间；与 decision_log 同源 */
  worker_last_scan_summary_at?: string | null;
  strong_count?: number;
  score_error?: number;
  no_signal?: number;
  ml_filter?: number;
  duplicate_guard?: number;
  last_manual_scan_error?: string | null;
  worker_updated_at?: string | null;
  /** Worker 内定时调度线程状态（与手动触发扫描区分） */
  scheduler_scan_in_progress?: boolean;
  scheduler_scan_started_at?: string | null;
  scheduler_scan_finished_at?: string | null;
  scheduler_last_error?: string | null;
  score_error_examples?: Array<{ symbol?: string; side?: string; reason?: string }>;
  invalid_symbol_errors?: string[];
};

type ApiHealthSnapshot = {
  window_minutes: number;
  total: number;
  errors: number;
  error_rate_pct: number;
  p95_ms: number;
  connection_errors: number;
  markets?: Record<
    string,
    {
      total: number;
      errors: number;
      error_rate_pct: number;
      p95_ms: number;
      connection_errors: number;
    }
  >;
};

type RestartEventItem = {
  ts?: string;
  event?: string;
  reason_code?: string;
  message?: string;
  fail_count?: number;
  pid_before?: number;
  pid_after?: number;
  unknown_pids?: number[];
  error?: string;
};

type RestartEventsResult = {
  count?: number;
  limit?: number;
  items?: RestartEventItem[];
};

const AUTO_TRADER_UI_CACHE_KEY = "lp_auto_trader_ui_cache_v1";

type AutoTraderUiCache = {
  liveStrongStocks?: any[];
  scanDiagnostics?: ScanDiagnostics | null;
};

function readAutoTraderUiCache(): AutoTraderUiCache | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(AUTO_TRADER_UI_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as AutoTraderUiCache) : null;
  } catch {
    return null;
  }
}

function writeAutoTraderUiCache(cache: AutoTraderUiCache): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(AUTO_TRADER_UI_CACHE_KEY, JSON.stringify(cache));
  } catch {
    // ignore cache write errors
  }
}

const EXIT_RULE_OPTIONS = [
  { key: "hard_stop", label: "硬止损" },
  { key: "take_profit", label: "止盈" },
  { key: "strategy_sell", label: "策略反转卖出" },
  { key: "time_stop", label: "时间止损" },
];

const joinSymbols = (rows?: string[]) => (rows || []).join("\n");
const joinPairs = (rows?: Record<string, string>) =>
  Object.entries(rows || {})
    .map(([a, b]) => `${a}=${b}`)
    .join("\n");
const parseSymbols = (text: string) =>
  Array.from(
    new Set(
      text
        .split(/[\n,]/g)
        .map((x) => x.trim().toUpperCase())
        .filter(Boolean)
    )
  );
const parsePairs = (text: string): Record<string, string> => {
  const out: Record<string, string> = {};
  text
    .split(/\n/g)
    .map((x) => x.trim().toUpperCase())
    .filter(Boolean)
    .forEach((line) => {
      const [left, right] = line.split("=").map((x) => x.trim());
      if (left && right && left !== right) out[left] = right;
    });
  return out;
};

const formatParamValue = (v: unknown): string => {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean" || typeof v === "number") return String(v);
  if (typeof v === "string") return v.length > 48 ? `${v.slice(0, 45)}…` : v;
  try {
    const j = JSON.stringify(v);
    return j.length > 56 ? `${j.slice(0, 53)}…` : j;
  } catch {
    return String(v);
  }
};

/** 参与评分策略标签旁展示的参数字符串（k=v 逗号分隔） */
const formatStrategyParamsParen = (
  strategyName: string,
  strategyParamsMap: Record<string, Record<string, unknown>> | undefined,
  defaultParams: Record<string, any> | undefined
): string => {
  const fromMap = strategyParamsMap?.[strategyName];
  const raw =
    fromMap && typeof fromMap === "object" && Object.keys(fromMap).length
      ? fromMap
      : defaultParams && typeof defaultParams === "object" && Object.keys(defaultParams).length
        ? defaultParams
        : null;
  if (!raw) return "";
  const parts = Object.entries(raw)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}=${formatParamValue(v)}`);
  return parts.length ? parts.join(", ") : "";
};

/** 信号行：从 strategy_params 或 trace 内嵌对象格式化为括号内展示串 */
const formatStrategyParamsRecord = (raw: Record<string, unknown> | undefined | null): string => {
  if (!raw || typeof raw !== "object") return "";
  const parts = Object.entries(raw)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}=${formatParamValue(v)}`);
  return parts.length ? parts.join(", ") : "";
};

const signalStrategyParams = (s: SignalItem): Record<string, unknown> | undefined => {
  const top = s.strategy_params;
  if (top && typeof top === "object" && Object.keys(top).length) return top;
  const tr = s.trace?.strategy_params;
  if (tr && typeof tr === "object" && Object.keys(tr).length) return tr as Record<string, unknown>;
  return undefined;
};

const INPUT_CLS =
  "w-full rounded-lg border border-slate-700/80 bg-slate-950/70 px-3 py-2 text-sm text-slate-100 outline-none ring-0 placeholder:text-slate-500 focus:border-cyan-400/70 focus:shadow-[0_0_0_1px_rgba(34,211,238,0.35)]";
/** 市场下拉：字号为常规输入框 text-sm 的两倍，便于辨认 */
const MARKET_SELECT_CLS = INPUT_CLS.replace("text-sm", "text-[1.75rem] font-medium");
const PANEL_TITLE_CLS = "text-sm font-semibold tracking-wide text-slate-100";
const SUB_TITLE_CLS = "text-[11px] uppercase tracking-[0.14em] text-slate-400";
const SUMMARY_CARD_CLS = "rounded-lg border border-slate-700/70 bg-slate-900/70 p-3 shadow-[0_8px_24px_rgba(2,6,23,0.25)]";

const formatTime = (value?: string | null) => {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", { hour12: false });
};

/** API 策略元数据与回测页 fallback 目录统一为勾选区用的 StrategyOption */
const catalogItemsToStrategyOptions = (items: StrategyCatalogItem[]): StrategyOption[] =>
  items.map((x) => ({
    name: x.name,
    label: x.label,
    description: x.description,
    default_params: x.default_params as Record<string, any>,
  }));

const TEMPLATE_LABEL_MAP: Record<string, string> = {
  trend: "趋势",
  mean_reversion: "均值回归",
  defensive: "防守",
  custom: "自定义",
};

const FIELD_LABEL_MAP: Record<string, string> = {
  active_template: "当前模板",
  entry_rule: "入场规则",
  breakout_lookback_bars: "突破回看周期",
  breakout_volume_ratio: "突破量能阈值",
  mean_reversion_rsi_threshold: "均值回归RSI阈值",
  mean_reversion_deviation_pct: "均值回归偏离阈值",
  exit_rules: "出场规则",
  rule_priority: "规则优先级",
  sizer: "仓位模型",
  cost_model: "成本模型",
  hard_stop_pct: "硬止损(%)",
  take_profit_pct: "止盈(%)",
  time_stop_hours: "时间止损(小时)",
  dry_run_mode: "演练模式",
  auto_execute: "自动执行",
  signal_relaxed_mode: "宽松信号模式",
  ml_filter_enabled: "ML过滤开关",
  ml_model_type: "ML模型",
  ml_threshold: "ML阈值",
  ml_horizon_days: "ML预测周期",
  ml_train_ratio: "ML训练比例",
  ml_walk_forward_windows: "ML Walk-forward窗口数",
  ml_filter_cache_minutes: "ML缓存分钟",
  research_allocation_enabled: "运用Research分配",
  research_allocation_max_age_minutes: "分配快照最大有效(分钟)",
  research_allocation_snapshot_id: "分配快照选择",
  research_allocation_notional_scale: "分配名义倍数",
  merge_strategy_matrix_top3: "并入矩阵优选前三",
  merge_strategy_matrix_top3_snapshot_id: "策略矩阵快照选择",
  strategy_params_map: "策略内参数映射",
};
const MATRIX_PROFILE_LABEL: Record<string, string> = {
  aggressive: "激进",
  balanced: "平衡",
  defensive: "保守",
  ranked: "排序",
  none: "无结果",
};

const TOKEN_LABEL_MAP: Record<string, string> = {
  strategy_cross: "策略交叉",
  breakout: "突破",
  mean_reversion: "均值回归",
  hard_stop: "硬止损",
  take_profit: "止盈",
  strategy_sell: "策略反转卖出",
  time_stop: "时间止损",
  fixed: "固定仓位",
  risk_percent: "风险比例仓位",
  volatility: "波动率仓位",
  buy: "买入",
  sell: "卖出",
  skipped: "跳过",
  pending: "待确认",
  executed: "已执行",
  simulated: "演练",
  failed: "失败",
};

const mapToken = (v: any): any => {
  if (typeof v !== "string") return v;
  return TOKEN_LABEL_MAP[v] || v;
};

const safeStringify = (v: any): string => {
  try {
    return JSON.stringify(v);
  } catch {
    return '"[不可序列化]"';
  }
};

const formatTemplateName = (name?: string) => TEMPLATE_LABEL_MAP[name || "custom"] || name || "自定义";

const RESEARCH_ALLOC_REASON_ZH: Record<string, string> = {
  disabled: "未开启",
  no_snapshot: "无快照",
  snapshot_stale: "快照过期",
  market_mismatch: "与当前市场不一致",
  empty_plan: "分配表为空",
  no_weights: "无有效权重",
  ok: "本轮已按权重裁剪买量",
  invalid_generated_at: "快照时间无效",
};

const formatResearchAllocReason = (r?: string) => RESEARCH_ALLOC_REASON_ZH[String(r || "")] || r || "-";

const formatDiffValue = (v: any): string => {
  if (Array.isArray(v)) return safeStringify(v.map(mapToken));
  if (v && typeof v === "object") {
    const copy: Record<string, any> = {};
    Object.entries(v).forEach(([k, vv]) => {
      copy[FIELD_LABEL_MAP[k] || k] = mapToken(vv);
    });
    return safeStringify(copy);
  }
  return safeStringify(mapToken(v));
};

const humanizeReason = (reason?: unknown) => {
  if (typeof reason !== "string" || !reason) return "-";
  if (reason.startsWith("entry_miss:")) {
    const code = reason.slice("entry_miss:".length);
    if (code === "no_buy_signal") return "入场未命中：当前无买入信号";
    if (code === "insufficient_bars") return "入场未命中：K线数据不足";
    if (code === "no_breakout") return "入场未命中：未突破前高";
    if (code === "breakout_no_volume") return "入场未命中：突破但量能不足";
    if (code === "no_mean_reversion_signal") return "入场未命中：均值回归条件未满足";
    return `入场未命中：${code}`;
  }
  if (reason.startsWith("exit_miss:")) {
    const code = reason.slice("exit_miss:".length);
    if (code === "no_exit_rule_hit") return "出场未命中：未触发任何卖出规则";
    if (code === "no_sell_signal") return "出场未命中：策略未给出卖出信号";
    return `出场未命中：${code}`;
  }
  if (reason.startsWith("guard_block:")) {
    const code = reason.slice("guard_block:".length);
    if (code.startsWith("symbol_cooldown")) return "风控拦截：同标的冷却中";
    if (code.startsWith("symbol_daily_limit")) return "风控拦截：同标的日内次数达上限";
    if (code === "existing_position_block") return "风控拦截：已有持仓，禁止自动加仓";
    if (code.startsWith("same_direction_new_order_limit")) return "风控拦截：单轮同向下单数已达上限";
    if (code.startsWith("max_concurrent_long_positions")) return "风控拦截：并发持仓数已达上限";
    return `风控拦截：${code}`;
  }
  if (reason === "has_active_signal") return "跳过：已有活跃信号";
  if (reason === "score_empty") return "跳过：策略评分为空";
  if (reason === "score_error") return "跳过：策略评分失败";
  if (reason === "dry_run_mode") return "演练模式：仅生成信号，不下单";
  if (reason === "strong_stock_best_strategy_signal") return "强势股最佳策略触发";
  if (reason === "position_best_strategy_sell_signal") return "持仓最佳策略卖出触发";
  if (reason === "breakout_confirmed") return "突破入场：突破前高且量能确认";
  if (reason === "mean_reversion_signal") return "均值回归入场：超跌+偏离满足";
  if (reason === "dry_run_buy_signal") return "演练买入信号";
  if (reason === "dry_run_sell_signal") return "演练卖出信号";
  return reason;
};

const humanizeBackupReason = (reason?: unknown) => {
  if (typeof reason !== "string" || !reason) return "-";
  if (reason === "update_config") return "配置更新自动备份";
  if (reason.startsWith("rollback_from:")) return `回滚前备份（来源 ${reason.replace("rollback_from:", "")}）`;
  return reason;
};

const humanizeRestartReasonCode = (code?: unknown) => {
  const v = String(code || "");
  if (!v) return "-";
  if (v === "health_timeout") return "健康检查连续失败";
  if (v === "port_conflict") return "端口被非托管进程占用";
  if (v === "restart_exception") return "重启过程异常";
  if (v === "backend_busy") return "后端繁忙保护（跳过重启）";
  if (v === "duplicate_watchdog_instance") return "重复 watchdog 实例退出";
  if (v === "pause_file") return "收到暂停标记，守护退出";
  return v;
};

const isTransientRequestError = (err: any): boolean => {
  const msg = String(err?.message || err || "").toLowerCase();
  return (
    msg.includes("请求超时") ||
    msg.includes("failed to fetch") ||
    msg.includes("networkerror") ||
    msg.includes("aborterror")
  );
};

export default function AutoTraderPage() {
  const [cachedSeed] = useState<AutoTraderUiCache | null>(() => readAutoTraderUiCache());
  const [status, setStatus] = useState<any>(null);
  const [cfg, setCfg] = useState<AutoConfig | null>(null);
  const [signals, setSignals] = useState<SignalItem[]>([]);
  const [executedSignals, setExecutedSignals] = useState<SignalItem[]>([]);
  const [error, setError] = useState("");
  const [softNotice, setSoftNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [restartingWorker, setRestartingWorker] = useState(false);
  const [saving, setSaving] = useState(false);
  const [switchingTemplate, setSwitchingTemplate] = useState(false);
  const [confirming, setConfirming] = useState<Record<string, boolean>>({});
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [backups, setBackups] = useState<ConfigBackupItem[]>([]);
  const [strategyOptions, setStrategyOptions] = useState<StrategyOption[]>([]);
  const [compactMode, setCompactMode] = useState(true);
  const [researchSnapshotHistory, setResearchSnapshotHistory] = useState<any[]>([]);
  const [strategyMatrixSnapshotHistory, setStrategyMatrixSnapshotHistory] = useState<any[]>([]);
  type MlApplyVariant = "auto" | "balanced" | "high_precision" | "high_coverage" | "best_score";
  const [mlMatrixSnapshotHistory, setMlMatrixSnapshotHistory] = useState<any[]>([]);
  const [mlApplyVariant, setMlApplyVariant] = useState<MlApplyVariant>("auto");
  const [mlApplySnapshotId, setMlApplySnapshotId] = useState<string>("");
  const [mlApplyBusy, setMlApplyBusy] = useState(false);
  /** 策略内参编辑弹层：策略 id，null 表示关闭 */
  const [strategyParamsModal, setStrategyParamsModal] = useState<string | null>(null);
  const [universeInput, setUniverseInput] = useState({ us: "", hk: "", cn: "" });
  const [pairInput, setPairInput] = useState({ us: "", hk: "", cn: "" });
  const [liveStrongStocks, setLiveStrongStocks] = useState<any[]>(
    Array.isArray(cachedSeed?.liveStrongStocks) ? cachedSeed!.liveStrongStocks! : []
  );
  const [scanDiagnostics, setScanDiagnostics] = useState<ScanDiagnostics | null>(cachedSeed?.scanDiagnostics || null);
  const lastGoodStrongStocksRef = useRef<any[]>(
    Array.isArray(cachedSeed?.liveStrongStocks) ? cachedSeed!.liveStrongStocks! : []
  );
  const lastGoodScanDiagnosticsRef = useRef<ScanDiagnostics | null>(cachedSeed?.scanDiagnostics || null);
  const [apiHealth, setApiHealth] = useState<ApiHealthSnapshot | null>(null);
  const [restartEvents, setRestartEvents] = useState<RestartEventItem[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // 用于防止自动刷新覆盖用户正在编辑的配置
  const isEditingRef = useRef(false);

  /** 与 autoBundle 同步逻辑一致；首屏 bootstrap 只 setCfg 不同步会导致文本框空白误保存清空股票池 */
  const syncPoolStateFromConfig = (nextCfg: AutoConfig | null) => {
    if (!nextCfg) return;
    if (nextCfg.universe) {
      setUniverseInput({
        us: joinSymbols(nextCfg.universe.us),
        hk: joinSymbols(nextCfg.universe.hk),
        cn: joinSymbols(nextCfg.universe.cn),
      });
    }
    if (nextCfg.pair_pool) {
      setPairInput({
        us: joinPairs(nextCfg.pair_pool.us),
        hk: joinPairs(nextCfg.pair_pool.hk),
        cn: joinPairs(nextCfg.pair_pool.cn),
      });
    }
  };

  const buildScanDiagnostics = (st: any, live?: any): ScanDiagnostics => {
    const sch = st?.runtime?.worker?.scheduler ?? st?.runtime?.scheduler;
    return {
    scan_time: live?.scan_time ?? st?.last_scan_summary?.scan_time ?? null,
    worker_last_scan_summary_at: live?.worker_last_scan_summary_at ?? (st?.last_scan_summary?.scan_time as string | null) ?? null,
    strong_count: live?.diagnostics?.strong_count ?? st?.last_scan_summary?.strong_count ?? 0,
    score_error: live?.diagnostics?.score_error ?? st?.last_scan_summary?.skipped?.score_error ?? 0,
    no_signal: live?.diagnostics?.no_signal ?? st?.last_scan_summary?.skipped?.no_signal ?? 0,
    ml_filter: live?.diagnostics?.ml_filter ?? st?.last_scan_summary?.skipped?.ml_filter ?? 0,
    duplicate_guard: live?.diagnostics?.duplicate_guard ?? st?.last_scan_summary?.skipped?.duplicate_guard ?? 0,
    last_manual_scan_error: live?.diagnostics?.last_manual_scan_error ?? st?.runtime?.worker?.last_manual_scan_error ?? null,
    worker_updated_at: live?.diagnostics?.worker_updated_at ?? st?.runtime?.worker?.updated_at ?? null,
    scheduler_scan_in_progress: Boolean(sch?.scan_in_progress),
    scheduler_scan_started_at: sch?.scan_started_at ?? null,
    scheduler_scan_finished_at: sch?.scan_finished_at ?? null,
    scheduler_last_error: sch?.last_error ?? null,
    score_error_examples:
      live?.diagnostics?.score_error_examples ??
      (st?.last_scan_summary?.decision_log || [])
        .filter((x: any) => String(x?.reason || "").includes("score_error"))
        .slice(0, 5)
        .map((x: any) => ({ symbol: x?.symbol, side: x?.side, reason: x?.reason })),
    invalid_symbol_errors: live?.diagnostics?.invalid_symbol_errors ?? (st?.last_scan_summary?.invalid_symbol_errors || []).slice(0, 5),
  };
  };

  useEffect(() => {
    let disposed = false;
    // 首屏先快速拉 status，避免页面其余重接口拖慢按钮可用时间。
    void apiGet<any>("/auto-trader/status", { timeoutMs: 8000, retries: 0, cacheTtlMs: 3000 })
      .then((st) => {
        if (disposed || !st) return;
        setStatus(st);
        const nextCfg = st?.config || null;
        if (nextCfg) {
          setCfg(nextCfg);
          syncPoolStateFromConfig(nextCfg);
        }
      })
      .catch(() => {
        // ignore bootstrap errors; full bundle polling will retry
      });
    return () => {
      disposed = true;
    };
  }, []);

  // Research allocation history：仅在启用时拉取
  useEffect(() => {
    if (!cfg?.market) return;
    if (!cfg?.research_allocation_enabled) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiGet<any>(
          `/auto-trader/research/snapshots?type=research&market=${encodeURIComponent(cfg.market)}`,
          { timeoutMs: 12000, retries: 0 }
        );
        const rows = Array.isArray(res?.snapshots) ? res.snapshots : [];
        if (!cancelled) setResearchSnapshotHistory(rows);
      } catch {
        // ignore
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cfg?.market, cfg?.research_allocation_enabled]);

  // TOP3 matrix merge history：仅在启用时拉取
  useEffect(() => {
    if (!cfg?.market) return;
    if (!cfg?.merge_strategy_matrix_top3) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await apiGet<any>(
          `/auto-trader/research/snapshots?type=strategy_matrix&market=${encodeURIComponent(cfg.market)}`,
          { timeoutMs: 12000, retries: 0 }
        );
        const rows = Array.isArray(res?.snapshots) ? res.snapshots : [];
        if (!cancelled) setStrategyMatrixSnapshotHistory(rows);
      } catch {
        // ignore
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cfg?.market, cfg?.merge_strategy_matrix_top3]);

  // ML 矩阵快照 history：用于“ML应用到AutoTrader”
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
        if (!cancelled) setMlMatrixSnapshotHistory(rows);
      } catch {
        // ignore: 默认允许“使用最新 ML 结果”
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [cfg?.market]);

  const { data: autoBundle, error: autoBundleError, mutate: mutateAutoBundle } = useSWR(
    "/auto-trader/page-bundle",
    async () => {
      const [st, sg, execSg, tpl, bkp] = await Promise.all([
        apiGet<any>("/auto-trader/status", { timeoutMs: 10000, retries: 0, cacheTtlMs: 3000 }),
        apiGet<any>("/auto-trader/signals?status=pending", { timeoutMs: 12000, retries: 1 }),
        apiGet<any>("/auto-trader/signals?status=executed", { timeoutMs: 12000, retries: 1 }),
        apiGet<any>("/auto-trader/templates", { timeoutMs: 10000, retries: 0 }),
        apiGet<any>("/auto-trader/config/backups", { timeoutMs: 12000, retries: 1 }),
      ]);
      let nextStrategyOptions: StrategyOption[] = catalogItemsToStrategyOptions(FALLBACK_STRATEGY_CATALOG);
      try {
        const stg = await apiGet<any>("/auto-trader/strategies", { timeoutMs: 10000, retries: 0 });
        const raw = stg?.items || [];
        if (Array.isArray(raw) && raw.length > 0) nextStrategyOptions = raw;
      } catch {}
      let nextApiHealth: ApiHealthSnapshot | null = null;
      try {
        const sla = await apiGet<any>("/auto-trader/metrics/sla?window_minutes=5&limit=2000", {
          timeoutMs: 10000,
          retries: 0,
        });
        const overall = sla?.overall || {};
        nextApiHealth = {
          window_minutes: Number(sla?.window_minutes || 5),
          total: Number(overall?.total || 0),
          errors: Number(overall?.errors || 0),
          error_rate_pct: Number(overall?.error_rate_pct || 0),
          p95_ms: Number(overall?.p95_ms || 0),
          connection_errors: Number(overall?.connection_errors || 0),
          markets: sla?.markets || {},
        };
      } catch {}
      let nextRestartEvents: RestartEventItem[] = [];
      try {
        const restarts = await apiGet<RestartEventsResult>("/ops/restarts/recent?limit=10", {
          timeoutMs: 8000,
          retries: 0,
        });
        nextRestartEvents = Array.isArray(restarts?.items) ? restarts.items : [];
      } catch {}
      const market = st?.config?.market || "us";
      const limit = st?.config?.top_n || 8;
      const kline = st?.config?.kline || "1d";
      let nextLiveStrongStocks = st?.last_scan_summary?.strong_stocks || [];
      let nextScanDiagnostics = buildScanDiagnostics(st);
      try {
        const live = await apiGet<any>(
          `/auto-trader/strong-stocks?market=${market}&limit=${limit}&kline=${kline}`,
          { timeoutMs: 12000, retries: 1 }
        );
        nextLiveStrongStocks = live?.items || [];
        nextScanDiagnostics = buildScanDiagnostics(st, live);
      } catch {}
      return {
        st,
        sg,
        execSg,
        tpl,
        bkp,
        nextStrategyOptions,
        nextApiHealth,
        nextRestartEvents,
        nextLiveStrongStocks,
        nextScanDiagnostics,
      };
    },
    buildSwrOptions(SWR_INTERVALS.mediumPoll.refreshInterval, SWR_INTERVALS.mediumPoll.dedupingInterval)
  );

  useEffect(() => {
    if (!autoBundle || isEditingRef.current) return;
    const st = autoBundle.st;
    setStatus(st);
    const nextCfg = st?.config || null;
    setCfg(nextCfg);
    syncPoolStateFromConfig(nextCfg);
    setSignals(autoBundle.sg?.items || []);
    setExecutedSignals(autoBundle.execSg?.items || []);
    setTemplates(autoBundle.tpl?.items || []);
    setBackups(autoBundle.bkp?.items || []);
    setStrategyOptions(autoBundle.nextStrategyOptions);
    setApiHealth(autoBundle.nextApiHealth);
    setRestartEvents(autoBundle.nextRestartEvents);
    const nextStrongStocks = Array.isArray(autoBundle.nextLiveStrongStocks) ? autoBundle.nextLiveStrongStocks : [];
    if (nextStrongStocks.length > 0) {
      setLiveStrongStocks(nextStrongStocks);
      lastGoodStrongStocksRef.current = nextStrongStocks;
    } else if (lastGoodStrongStocksRef.current.length > 0) {
      // 抖动或短时空结果时保留上次有效强势股，避免页面“闪空”。
      setLiveStrongStocks(lastGoodStrongStocksRef.current);
    } else {
      setLiveStrongStocks([]);
    }
    const nextDiag = (autoBundle.nextScanDiagnostics || null) as ScanDiagnostics | null;
    if (nextDiag) {
      setScanDiagnostics(nextDiag);
      lastGoodScanDiagnosticsRef.current = nextDiag;
    } else if (lastGoodScanDiagnosticsRef.current) {
      setScanDiagnostics(lastGoodScanDiagnosticsRef.current);
    } else {
      setScanDiagnostics(null);
    }
    writeAutoTraderUiCache({
      liveStrongStocks: lastGoodStrongStocksRef.current,
      scanDiagnostics: lastGoodScanDiagnosticsRef.current,
    });
    setSoftNotice("");
    setError("");
  }, [autoBundle]);

  useEffect(() => {
    if (!autoBundleError) return;
    const msg = String((autoBundleError as any)?.message || autoBundleError || "");
    if (isTransientRequestError(autoBundleError)) {
      setSoftNotice(
        "服务预热中或网络短时抖动，正在自动重试。你可以继续操作，数据会在后台恢复后自动刷新。"
      );
      return;
    }
    setError(msg);
  }, [autoBundleError]);

  const load = async (force = false) => {
    if (isEditingRef.current && !force) return;
    await mutateAutoBundle();
  };

  useEffect(() => {
    if (!strategyParamsModal) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setStrategyParamsModal(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [strategyParamsModal]);

  // 当用户开始编辑配置时调用
  const startEditing = () => {
    isEditingRef.current = true;
  };

  // 当用户保存或取消编辑时调用
  const stopEditing = () => {
    isEditingRef.current = false;
  };

  const saveConfig = async () => {
    if (!cfg) return;
    setSaving(true);
    try {
      let universe = {
        us: parseSymbols(universeInput.us),
        hk: parseSymbols(universeInput.hk),
        cn: parseSymbols(universeInput.cn),
      };
      const universeInputsAllEmpty =
        universe.us.length === 0 && universe.hk.length === 0 && universe.cn.length === 0;
      const cfgU = cfg.universe;
      const cfgHasUniverse =
        !!cfgU &&
        (cfgU.us?.length || 0) + (cfgU.hk?.length || 0) + (cfgU.cn?.length || 0) > 0;
      if (universeInputsAllEmpty && cfgHasUniverse && cfgU) {
        universe = {
          us: [...(cfgU.us || [])],
          hk: [...(cfgU.hk || [])],
          cn: [...(cfgU.cn || [])],
        };
      }

      let pair_pool = {
        us: parsePairs(pairInput.us),
        hk: parsePairs(pairInput.hk),
        cn: parsePairs(pairInput.cn),
      };
      const pairInputsAllEmpty =
        Object.keys(pair_pool.us).length === 0 &&
        Object.keys(pair_pool.hk).length === 0 &&
        Object.keys(pair_pool.cn).length === 0;
      const cfgP = cfg.pair_pool;
      const cfgHasPairs =
        !!cfgP &&
        Object.keys(cfgP.us || {}).length +
          Object.keys(cfgP.hk || {}).length +
          Object.keys(cfgP.cn || {}).length >
          0;
      if (pairInputsAllEmpty && cfgHasPairs && cfgP) {
        pair_pool = {
          us: { ...(cfgP.us || {}) },
          hk: { ...(cfgP.hk || {}) },
          cn: { ...(cfgP.cn || {}) },
        };
      }

      const payload = {
        ...cfg,
        universe,
        pair_pool,
      };
      const d = await apiPost<any>("/auto-trader/config", payload);
      setCfg(d?.config || cfg);
      await load();
      setError("");
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setSaving(false);
      stopEditing(); // 保存完成后恢复自动刷新
    }
  };

  const applyMlMatrixToConfig = async () => {
    if (!cfg) return;
    // 覆盖当前配置前先恢复自动刷新，避免用户误把页面卡在编辑态。
    stopEditing();
    if (
      !confirm(
        "将 ML 矩阵选中的最优（或指定 variant）参数合并到自动交易配置，并默认开启 ML 过滤。\n若使用独立 Worker，请重启 Worker 后生效。\n是否继续？"
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
      setSoftNotice(String(res?.message || "已应用 ML 最优参数到 AutoTrader"));
      await load(true);
    } catch (e: any) {
      let detail = String(e?.message || e);
      try {
        const j = JSON.parse(detail);
        const d = j?.detail;
        if (typeof d === "string") detail = d;
        else if (d && typeof d === "object") detail = JSON.stringify(d, null, 2);
      } catch {
        // keep
      }
      setError(`应用 ML 配置失败: ${detail}`);
    } finally {
      setMlApplyBusy(false);
    }
  };

  const applyTemplate = async (name: TemplateItem["name"]) => {
    let preview: any = null;
    try {
      preview = await apiGet<any>(`/auto-trader/template/preview?name=${encodeURIComponent(name)}`);
    } catch (e: any) {
      setError(String(e.message || e));
      return;
    }
    const diffRows = Object.entries(preview?.diff || {}).map(([k, v]: any) => {
      const from = formatDiffValue(v?.from ?? null);
      const to = formatDiffValue(v?.to ?? null);
      return `${FIELD_LABEL_MAP[k] || k}: ${from} -> ${to}`;
    });
    const diffText = diffRows.length ? diffRows.slice(0, 12).join("\n") : "无参数变化";
    if (!confirm(`模板预览：${preview?.label || name}\n\n${diffText}\n\n确认继续？`)) return;
    if (!confirm("二次确认：将写入配置并覆盖对应参数，是否执行？")) return;
    setSwitchingTemplate(true);
    try {
      await apiPost<any>("/auto-trader/template/apply", { name });
      await load(true);
      setError("");
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setSwitchingTemplate(false);
    }
  };

  const exportConfig = async () => {
    try {
      const data = await apiGet<any>("/auto-trader/config/export");
      const blob = new Blob([JSON.stringify(data?.config || {}, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `auto-trader-config-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  const importConfig = async (file?: File | null) => {
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      await apiPost<any>("/auto-trader/config/import", { config: parsed });
      await load(true);
      setError("");
    } catch (e: any) {
      setError(`导入失败: ${String(e.message || e)}`);
    }
  };

  const rollbackConfig = async (backupId: string) => {
    let preview: any = null;
    try {
      preview = await apiGet<any>(`/auto-trader/config/rollback/preview?backup_id=${encodeURIComponent(backupId)}`);
    } catch (e: any) {
      setError(String(e.message || e));
      return;
    }
    const diffRows = Object.entries(preview?.diff || {}).map(([k, v]: any) => {
      const from = formatDiffValue(v?.from ?? null);
      const to = formatDiffValue(v?.to ?? null);
      return `${FIELD_LABEL_MAP[k] || k}: ${from} -> ${to}`;
    });
    const diffText = diffRows.length ? diffRows.slice(0, 12).join("\n") : "无参数变化";
    if (!confirm(`回滚预览 ${backupId}\n\n${diffText}\n\n确认继续？`)) return;
    if (!confirm("二次确认：将覆盖当前配置，是否执行回滚？")) return;
    try {
      await apiPost<any>("/auto-trader/config/rollback", { backup_id: backupId });
      await load(true);
      setError("");
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  const runScan = async () => {
    setBusy(true);
    try {
      let recoveredAfterTimeout = false;
      try {
        await apiPost<any>("/auto-trader/scan/run", {}, { timeoutMs: 12000, retries: 0 });
      } catch (startErr: any) {
        if (!isTransientRequestError(startErr)) throw startErr;
        // 超时后主动探测 worker 运行态；若在运行则视为请求大概率已被接收，避免误报失败。
        const st = await apiGet<any>("/auto-trader/status", { timeoutMs: 8000, retries: 0, cacheTtlMs: 0 });
        if (!st?.running) throw startErr;
        recoveredAfterTimeout = true;
        setError("手动扫描请求超时，已自动切换为后台跟踪模式；若 Worker 正常将很快开始扫描。");
      }
      // 手动扫描完成后需要强制刷新，避免被“编辑中”状态拦截
      await load(true);
      if (!recoveredAfterTimeout) {
        setError("");
      }
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  const restartWorker = async () => {
    if (!cfg) return;
    if (!cfg.enabled) {
      setError("请先开启“启用自动扫描”，再重启 Worker。");
      return;
    }
    setRestartingWorker(true);
    const prevScanAt = status?.last_scan_summary?.scan_time || scanDiagnostics?.worker_last_scan_summary_at || null;
    try {
      // 只重启 worker：停 auto_trader 进程，不触碰飞书
      await apiPost<any>("/setup/services/stop", { stop_auto_trader: true, stop_feishu_bot: false });

      // 等待 worker_running 变为 false（防止旧进程仍在扫旧配置）
      for (let i = 0; i < 20; i++) {
        const st = await apiGet<any>("/auto-trader/status", { timeoutMs: 8000, retries: 0, cacheTtlMs: 0 });
        if (!st?.running) break;
        await new Promise((r) => setTimeout(r, 1000));
      }

      await apiPost<any>("/setup/services/start", { enable_auto_trader: true, start_feishu_bot: false });

      // 等待 worker 重新运行并至少触发一次新的完整扫描（或超时后仍刷新页面）
      for (let i = 0; i < 25; i++) {
        const st = await apiGet<any>("/auto-trader/status", { timeoutMs: 8000, retries: 0, cacheTtlMs: 0 });
        const nowScanAt = st?.last_scan_summary?.scan_time || null;
        if (st?.running && nowScanAt && (!prevScanAt || nowScanAt !== prevScanAt)) {
          break;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }

      await load(true);
      setError("");
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setRestartingWorker(false);
    }
  };

  const toggleStrategy = (name: string) => {
    if (!cfg) return;
    const s = new Set(cfg.strategies || []);
    if (s.has(name)) s.delete(name);
    else s.add(name);
    setCfg({ ...cfg, strategies: Array.from(s) });
  };

  const patchStrategyParam = (strategyName: string, paramKey: string, raw: string) => {
    if (!cfg) return;
    startEditing();
    const base = { ...(cfg.strategy_params_map || {}) };
    const prev = (
      base[strategyName] && typeof base[strategyName] === "object" ? base[strategyName] : {}
    ) as Record<string, unknown>;
    const cur = { ...prev };
    const t = raw.trim();
    if (t === "") delete cur[paramKey];
    else {
      const n = Number(t);
      if (!Number.isFinite(n)) return;
      cur[paramKey] = n;
    }
    if (Object.keys(cur).length === 0) delete base[strategyName];
    else base[strategyName] = cur;
    setCfg({ ...cfg, strategy_params_map: base });
  };

  const resetStrategyParams = (strategyName: string) => {
    if (!cfg) return;
    startEditing();
    const base = { ...(cfg.strategy_params_map || {}) };
    delete base[strategyName];
    setCfg({ ...cfg, strategy_params_map: base });
  };

  const paramKeysForStrategy = (name: string): string[] => {
    const opt = strategyOptions.find((x) => x.name === name);
    const defaults = opt?.default_params;
    const saved = cfg?.strategy_params_map?.[name];
    const keys = new Set<string>();
    if (defaults && typeof defaults === "object") Object.keys(defaults).forEach((k) => keys.add(k));
    if (saved && typeof saved === "object") Object.keys(saved).forEach((k) => keys.add(k));
    return Array.from(keys).sort();
  };

  const displayParamInputValue = (name: string, key: string): string => {
    const opt = strategyOptions.find((x) => x.name === name);
    const defaults = (opt?.default_params || {}) as Record<string, unknown>;
    const saved = cfg?.strategy_params_map?.[name] as Record<string, unknown> | undefined;
    const hasSaved = saved && Object.prototype.hasOwnProperty.call(saved, key);
    const v = hasSaved ? saved![key] : defaults[key];
    if (v === undefined || v === null) return "";
    const n = Number(v);
    return Number.isFinite(n) ? String(n) : String(v);
  };

  const strategyCandidates = strategyOptions.length
    ? strategyOptions.map((x) => x.name)
    : Array.from(new Set(cfg?.strategies || []));

  const confirmSignal = async (signalId: string) => {
    if (!confirm(`确认执行信号 ${signalId} 的下单？`)) return;
    setConfirming((x) => ({ ...x, [signalId]: true }));
    try {
      await apiPost<any>(`/auto-trader/signals/${encodeURIComponent(signalId)}/confirm`, {});
      await load();
      setError("");
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setConfirming((x) => ({ ...x, [signalId]: false }));
    }
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1">
            <h1 className="text-2xl font-bold tracking-tight text-white">Auto Trader 控制台</h1>
            <p className="text-sm text-slate-300">
              {cfg?.auto_execute ? "全自动交易模式" : "半自动交易模式"} · 策略择优 · ETF 配对组合 · 风控保护
            </p>
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <span
              className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ${
                status?.running
                  ? "bg-emerald-500/20 text-emerald-400 ring-1 ring-emerald-500/30"
                  : "bg-slate-500/20 text-slate-400 ring-1 ring-slate-500/30"
              }`}
            >
              {status?.running ? "运行中" : "未运行"}
            </span>
            <span className="inline-flex items-center rounded-full bg-blue-500/20 px-3 py-1 text-xs font-medium text-blue-300 ring-1 ring-blue-500/30">
              {cfg?.enabled ? "自动扫描已启用" : "自动扫描未启用"}
            </span>
            <span className="inline-flex items-center rounded-full bg-fuchsia-500/20 px-3 py-1 text-xs font-medium text-fuchsia-300 ring-1 ring-fuchsia-500/30">
              {cfg?.pair_mode ? "ETF配对模式" : "强势股模式"}
            </span>
            {cfg?.auto_execute ? (
              <span className="inline-flex items-center rounded-full bg-amber-500/20 px-3 py-1 text-xs font-medium text-amber-300 ring-1 ring-amber-500/30">
                全自动
              </span>
            ) : (
              <span className="inline-flex items-center rounded-full bg-slate-500/20 px-3 py-1 text-xs font-medium text-slate-300 ring-1 ring-slate-500/30">
                半自动
              </span>
            )}
            <div className="ml-1 inline-flex rounded-lg border border-slate-700/80 bg-slate-900/70 p-0.5">
              <button
                type="button"
                onClick={() => setCompactMode(true)}
                className={`rounded-md px-2 py-1 text-xs transition ${
                  compactMode ? "bg-cyan-500/20 text-cyan-200" : "text-slate-400 hover:text-slate-200"
                }`}
                title="聚焦常用参数，隐藏大部分高级设置"
              >
                简洁模式
              </button>
              <button
                type="button"
                onClick={() => setCompactMode(false)}
                className={`rounded-md px-2 py-1 text-xs transition ${
                  !compactMode ? "bg-indigo-500/20 text-indigo-200" : "text-slate-400 hover:text-slate-200"
                }`}
                title="显示全部参数与高级配置"
              >
                专家模式
              </button>
            </div>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-6">
          <div className={SUMMARY_CARD_CLS}>
            <div className={SUB_TITLE_CLS}>待确认信号</div>
            <div className="mt-1 text-xl font-semibold text-amber-300">{signals.length}</div>
          </div>
          <div className={SUMMARY_CARD_CLS}>
            <div className={SUB_TITLE_CLS}>已执行信号</div>
            <div className="mt-1 text-xl font-semibold text-emerald-300">{executedSignals.length}</div>
          </div>
          <div className={SUMMARY_CARD_CLS}>
            <div className={SUB_TITLE_CLS}>今日交易</div>
            <div className="mt-1 text-xl font-semibold text-cyan-300">
              {status?.daily_trade_count ?? 0} / {cfg?.max_daily_trades ?? 5}
            </div>
          </div>
          <div className={SUMMARY_CARD_CLS}>
            <div className={SUB_TITLE_CLS}>本轮强势股</div>
            <div className="mt-1 text-xl font-semibold text-cyan-300">{liveStrongStocks.length || status?.last_scan_summary?.strong_count || 0}</div>
          </div>
          <div className={SUMMARY_CARD_CLS}>
            <div className={SUB_TITLE_CLS}>上次扫描</div>
            <div className="mt-1 text-sm font-semibold text-slate-200">{formatTime(status?.last_scan_at)}</div>
          </div>
          <div className={SUMMARY_CARD_CLS}>
            <div className={SUB_TITLE_CLS}>API健康(5分钟)</div>
            {apiHealth ? (
              <div className="space-y-1">
                <div
                  className={`mt-1 text-sm font-semibold ${
                    apiHealth.error_rate_pct <= 5
                      ? "text-emerald-300"
                      : apiHealth.error_rate_pct <= 20
                        ? "text-amber-300"
                        : "text-rose-300"
                  }`}
                >
                  错误率 {apiHealth.error_rate_pct}%
                </div>
                <div className="text-xs text-slate-300">p95: {apiHealth.p95_ms}ms</div>
                <div className="text-xs text-slate-400">
                  样本 {apiHealth.total} / 连接异常 {apiHealth.connection_errors}
                </div>
                <div className="text-[11px] text-slate-400">
                  US {apiHealth.markets?.us?.error_rate_pct ?? "-"}% · HK {apiHealth.markets?.hk?.error_rate_pct ?? "-"}% · CN {apiHealth.markets?.cn?.error_rate_pct ?? "-"}%
                </div>
              </div>
            ) : (
              <div className="mt-1 text-xs text-slate-400">暂无数据</div>
            )}
          </div>
        </div>
      </div>

      {error ? (
        <div className="panel border-rose-200 bg-rose-50 text-rose-700">
          <div className={SUB_TITLE_CLS}>错误信息</div>
          <div className="mt-1 text-sm">{error}</div>
        </div>
      ) : null}
      {!error && softNotice ? (
        <div className="panel border-amber-300/60 bg-amber-50 text-amber-800">
          <div className={SUB_TITLE_CLS}>提示</div>
          <div className="mt-1 text-sm">{softNotice}</div>
        </div>
      ) : null}

      {cfg?.enabled && !status?.running ? (
        <div className="panel border-amber-500/40 bg-amber-950/30 text-amber-100">
          <div className="text-sm font-medium text-amber-200">独立 Worker 未在运行</div>
          <p className="mt-1 text-xs text-amber-100/90">
            配置里已启用自动扫描，但扫描/下单在<strong className="text-amber-200">独立进程</strong>中执行。请<strong>保存一次配置</strong>（或重启
            API）以自动拉起 Supervisor；也可到{" "}
            <Link href="/setup" className="text-cyan-300 underline hover:text-cyan-200">
              设置 → 启动服务
            </Link>{" "}
            手动启动自动交易进程。仅启动 API 不会持续更新「worker 更新时间」。
          </p>
        </div>
      ) : null}

      <div className="panel space-y-3">
        <div className={PANEL_TITLE_CLS}>运行状态</div>
        <div className="text-sm">
          扫描线程：{status?.running ? <span className="text-emerald-400">运行中</span> : <span className="text-slate-400">未运行</span>}
        </div>
        <div className="text-sm">交易模式：{cfg?.auto_execute ? <span className="text-amber-400">全自动（信号生成后立即执行）</span> : <span className="text-slate-400">半自动（需人工确认）</span>}</div>
        <div className="text-sm">演练模式：{cfg?.dry_run_mode ? <span className="text-fuchsia-300">开启（只生成信号，不下单）</span> : <span className="text-slate-400">关闭</span>}</div>
        <div className="text-sm">
          Research 分配（执行层）：
          {cfg?.research_allocation_enabled ? (
            <span className="text-cyan-300">配置已开启</span>
          ) : (
            <span className="text-slate-400">关闭</span>
          )}
          {" · "}
          上轮扫描：
          <span className="text-slate-200">
            {formatResearchAllocReason((status as any)?.last_scan_summary?.research_allocation?.scan_reason)}
          </span>
          {(status as any)?.last_scan_summary?.research_allocation?.scan_applied ? (
            <span className="text-emerald-300">
              {" "}
              （已应用 · 表内标的数 {(status as any)?.last_scan_summary?.research_allocation?.weights_symbol_count ?? 0}）
            </span>
          ) : null}
          {(status as any)?.last_scan_summary?.research_allocation?.snapshot_meta?.generated_at ? (
            <span className="text-xs text-slate-500">
              {" "}
              · 快照 {formatTime((status as any).last_scan_summary.research_allocation.snapshot_meta.generated_at)}
            </span>
          ) : null}
        </div>
        {(status as any)?.research_allocation?.worker_last ? (
          <div className="text-xs text-slate-500">
            Worker 最近分配上下文：{formatResearchAllocReason((status as any).research_allocation.worker_last.reason)} · 标的数{" "}
            {(status as any).research_allocation.worker_last.symbol_count ?? "-"}
          </div>
        ) : null}
        <div className="text-sm">当前模板：<span className="text-cyan-300">{formatTemplateName(cfg?.active_template || "custom")}</span></div>
        <div className="text-sm">信号模式：{cfg?.signal_relaxed_mode ? <span className="text-cyan-300">宽松（当前为buy即触发）</span> : <span className="text-slate-300">严格（仅新触发 buy）</span>}</div>
        <div className="text-sm">自动卖出：{cfg?.auto_sell_enabled ? <span className="text-rose-300">开启</span> : <span className="text-slate-400">关闭</span>}</div>
        <div className="text-sm">上次扫描：{formatTime(status?.last_scan_at)}</div>
        <div className="text-sm">
          连续无信号：<span className="text-amber-300">{status?.consecutive_no_signal_rounds ?? 0}</span>
          {" | "}
          观察模式：{cfg?.observer_mode_enabled !== false ? <span className="text-emerald-300">开启</span> : <span className="text-slate-400">关闭</span>}
          {" | "}
          阈值N：<span className="text-cyan-300">{cfg?.observer_no_signal_rounds ?? 3}</span>
        </div>
        <div className="text-sm">
          连亏停机：
          {status?.consecutive_loss_stop_enabled === false ? (
            <span className="text-slate-400">关闭</span>
          ) : (
            <span className={status?.consecutive_loss_stop_triggered ? "text-rose-300" : "text-emerald-300"}>
              {status?.consecutive_loss_stop_triggered ? "已触发" : "监控中"}
            </span>
          )}
          {" | "}
          连亏计数：<span className="text-amber-300">{status?.consecutive_loss_count ?? 0}</span> /{" "}
          <span className="text-cyan-300">{status?.consecutive_loss_stop_count ?? 3}</span>
          {" | "}
          最近估算盈亏：<span className="text-slate-300">{typeof status?.last_trade_pnl_estimate === "number" ? status.last_trade_pnl_estimate.toFixed(2) : "-"}</span>
        </div>
        {status?.consecutive_loss_stop_reason ? (
          <div className="text-xs text-rose-300">
            连亏停机原因：{status.consecutive_loss_stop_reason}
            {status?.consecutive_loss_stop_at ? `（${formatTime(status.consecutive_loss_stop_at)}）` : ""}
          </div>
        ) : null}
        {status?.last_observer_push_at ? (
          <div className="text-xs text-slate-400">上次观察提示推送：{formatTime(status.last_observer_push_at)}</div>
        ) : null}
        {status?.last_scan_summary?.skipped ? (
          <div className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3 text-xs text-slate-300">
            <div className="font-medium text-slate-200">上次扫描跳过统计</div>
            <div className="mt-1">
              无信号: {status.last_scan_summary.skipped.no_signal ?? 0} | 评分失败: {status.last_scan_summary.skipped.score_error ?? 0} | 已有活跃信号: {status.last_scan_summary.skipped.has_active_signal ?? 0} | 异常: {status.last_scan_summary.skipped.exception ?? 0}
            </div>
            <div className="mt-1">
              防重单拦截: {status.last_scan_summary.skipped.duplicate_guard ?? 0} | ML过滤拦截: {status.last_scan_summary.skipped.ml_filter ?? 0}
            </div>
            {(status?.last_scan_summary?.invalid_symbol_errors?.length || 0) > 0 ? (
              <div className="mt-2 text-rose-300">
                无效代码: {status.last_scan_summary.invalid_symbol_errors.slice(0, 3).join(" ; ")}
              </div>
            ) : null}
            {(status?.last_scan_summary?.pruned_invalid_symbols?.length || 0) > 0 ? (
              <div className="mt-1 text-emerald-300">
                已自动剔除: {status.last_scan_summary.pruned_invalid_symbols.join(", ")}
              </div>
            ) : null}
          </div>
        ) : null}
        {(status?.last_scan_summary?.decision_log || []).length > 0 ? (
          <div className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3 text-xs text-slate-300">
            <div className="font-medium text-slate-200">本轮决策链路（为何买/卖/跳过）</div>
            <div className="mt-1 text-slate-400">
              数据时间（Worker 完整扫描）：{" "}
              <span className="text-slate-200">{formatTime(status?.last_scan_summary?.scan_time)}</span>
            </div>
            <div className="mt-1 text-slate-400">
              {(() => {
                const byMarket = status?.last_scan_summary?.scan_round_in_day_by_market || {};
                const us = byMarket?.us ?? "-";
                const hk = byMarket?.hk ?? "-";
                const cn = byMarket?.cn ?? "-";
                return (
                  <>
                    今日扫描轮次：{" "}
                    <span className="text-slate-200">US {us}</span>
                    {" / "}
                    <span className="text-slate-200">HK {hk}</span>
                    {" / "}
                    <span className="text-slate-200">CN {cn}</span>
                  </>
                );
              })()}
            </div>
            <p className="mt-1 leading-relaxed text-slate-500">
              说明：列表仅随独立 Worker 每次<strong className="text-slate-400">完整扫描</strong>更新。上方「本轮强势股」可能来自
              API 即时筛选与短周期缓存刷新，与决策链时间不同步属正常现象。
            </p>
            <div className="mt-2 max-h-48 space-y-1 overflow-auto pr-1">
              {(status.last_scan_summary.decision_log || []).slice(0, 30).map((d: any, idx: number) => (
                <div key={`${String(d?.symbol || "UNKNOWN")}-${idx}`} className="rounded border border-slate-700/70 bg-slate-900/70 px-2 py-1">
                  <span className="text-cyan-300">{String(d?.symbol || "-")}</span>
                  {" | "}
                  <span className="text-slate-200">{TOKEN_LABEL_MAP[String(d?.side || "")] || String(d?.side || "-")}</span>
                  {" | "}
                  <span className={d?.result === "skipped" ? "text-amber-300" : d?.result === "executed" ? "text-emerald-300" : "text-blue-300"}>
                    {TOKEN_LABEL_MAP[String(d?.result || "")] || String(d?.result || "-")}
                  </span>
                  {" | "}
                  <span>{humanizeReason(d?.reason)}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      {!compactMode ? (
        <div className="panel space-y-3">
          <details className="group rounded-lg border border-slate-700/70 bg-slate-900/30">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2">
            <div className="flex items-center gap-2">
              <div className={PANEL_TITLE_CLS}>最近重启原因（Watchdog）</div>
              <div className="text-xs text-slate-400">最近 10 条</div>
            </div>
            <span className="text-xs text-slate-400 group-open:hidden">展开</span>
            <span className="hidden text-xs text-slate-400 group-open:inline">收起</span>
          </summary>
          <div className="border-t border-slate-800/80 p-3">
            {!restartEvents.length ? (
              <div className="text-sm text-slate-400">暂无重启事件（当前窗口内可能没有发生重启）。</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[780px] text-xs">
                  <thead className="text-left text-slate-400">
                    <tr>
                      <th className="px-2 py-2">时间</th>
                      <th className="px-2 py-2">事件</th>
                      <th className="px-2 py-2">原因码</th>
                      <th className="px-2 py-2">PID变化</th>
                      <th className="px-2 py-2">失败计数</th>
                      <th className="px-2 py-2">摘要</th>
                    </tr>
                  </thead>
                  <tbody>
                    {restartEvents.map((x, idx) => (
                      <tr key={`${x.ts || "na"}-${x.event || "event"}-${idx}`} className="border-t border-slate-800/80 text-slate-300">
                        <td className="px-2 py-2">{formatTime(x.ts)}</td>
                        <td className="px-2 py-2">
                          <span className="rounded bg-slate-800 px-2 py-0.5 text-[11px] text-cyan-300">
                            {x.event || "-"}
                          </span>
                        </td>
                        <td className="px-2 py-2">{humanizeRestartReasonCode(x.reason_code)}</td>
                        <td className="px-2 py-2">
                          {typeof x.pid_before === "number" || typeof x.pid_after === "number"
                            ? `${x.pid_before ?? "-"} -> ${x.pid_after ?? "-"}`
                            : "-"}
                        </td>
                        <td className="px-2 py-2">{typeof x.fail_count === "number" ? x.fail_count : "-"}</td>
                        <td className="px-2 py-2 text-slate-400">{x.message || x.error || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          </details>
        </div>
      ) : null}

      {cfg ? (
        <div className="panel space-y-4" onClick={startEditing}>
          <div className={PANEL_TITLE_CLS}>基础配置 <span className="text-xs font-normal text-amber-400">{isEditingRef.current ? "(编辑中，自动刷新已暂停)" : ""}</span></div>
          <div className="text-sm font-medium text-slate-200">策略参数配置</div>
          <div className="grid grid-cols-1 gap-2 rounded-lg border border-slate-700/70 bg-slate-900/40 p-3 md:grid-cols-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={cfg.enabled}
                onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
              />
              启用自动扫描
            </label>
            <label className="flex items-center gap-2 text-sm" title={cfg.pair_mode ? "已含 ETF 配对模式下的自动下单放行" : undefined}>
              <input
                type="checkbox"
                checked={
                  cfg.pair_mode ? !!cfg.auto_execute && !!cfg.pair_mode_allow_auto_execute : !!cfg.auto_execute
                }
                onChange={(e) => {
                  const v = e.target.checked;
                  if (cfg.pair_mode) {
                    setCfg({ ...cfg, auto_execute: v, pair_mode_allow_auto_execute: v });
                  } else {
                    setCfg({ ...cfg, auto_execute: v });
                  }
                }}
              />
              全自动执行（无需确认）
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={cfg.pair_mode}
                onChange={(e) => {
                  const on = e.target.checked;
                  setCfg({
                    ...cfg,
                    pair_mode: on,
                    ...(on && cfg.auto_execute ? { pair_mode_allow_auto_execute: true } : {}),
                  });
                }}
              />
              ETF配对模式
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={!!cfg.signal_relaxed_mode}
                onChange={(e) => setCfg({ ...cfg, signal_relaxed_mode: e.target.checked })}
              />
              宽松信号模式
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={cfg.auto_prune_invalid_symbols !== false}
                onChange={(e) => setCfg({ ...cfg, auto_prune_invalid_symbols: e.target.checked })}
              />
              自动剔除无效代码
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={cfg.observer_mode_enabled !== false}
                onChange={(e) => setCfg({ ...cfg, observer_mode_enabled: e.target.checked })}
              />
              观察模式提示
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={!!cfg.auto_sell_enabled}
                onChange={(e) => setCfg({ ...cfg, auto_sell_enabled: e.target.checked })}
              />
              启用自动卖出
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={!!cfg.dry_run_mode}
                onChange={(e) => setCfg({ ...cfg, dry_run_mode: e.target.checked })}
              />
              只读演练模式（不下单）
            </label>

            <select
              className={MARKET_SELECT_CLS}
              value={cfg.market}
              onChange={(e) => setCfg({ ...cfg, market: e.target.value as "us" | "hk" | "cn" })}
            >
              <option value="us">美股</option>
              <option value="hk">港股</option>
              <option value="cn">A股</option>
            </select>

            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">扫描间隔(秒)</div>
              <input
                className={INPUT_CLS}
                type="number"
                value={cfg.interval_seconds}
                onChange={(e) => setCfg({ ...cfg, interval_seconds: Number(e.target.value) })}
                placeholder="扫描间隔(秒)"
              />
            </label>

            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">强势股数量</div>
              <input
                className={INPUT_CLS}
                type="number"
                value={cfg.top_n}
                onChange={(e) => setCfg({ ...cfg, top_n: Number(e.target.value) })}
                placeholder="强势股数量"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">观察提示阈值N（连续无信号轮数）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                max={50}
                value={cfg.observer_no_signal_rounds ?? 3}
                onChange={(e) => setCfg({ ...cfg, observer_no_signal_rounds: Number(e.target.value) })}
                placeholder="连续N轮无信号触发提醒"
              />
            </label>
          </div>

          <details className="rounded-lg border border-slate-700/70 bg-slate-900/30 p-3" open={!compactMode}>
            <summary className="cursor-pointer list-none text-sm font-semibold tracking-wide text-slate-100">
              风控配置
              <span className="ml-2 text-xs font-normal text-slate-400">
                {compactMode ? "（默认折叠）" : "（已展开）"}
              </span>
            </summary>
            <div className="mt-3 grid grid-cols-1 gap-2 rounded-lg border border-slate-700/70 bg-slate-900/40 p-3 md:grid-cols-4">
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">每日最大交易次数</div>
              <input
                className={INPUT_CLS}
                type="number"
                value={cfg.max_daily_trades}
                onChange={(e) => setCfg({ ...cfg, max_daily_trades: Number(e.target.value) })}
                placeholder="每日最大交易次数"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">单个持仓最大市值($)</div>
              <input
                className={INPUT_CLS}
                type="number"
                value={cfg.max_position_value}
                onChange={(e) => setCfg({ ...cfg, max_position_value: Number(e.target.value) })}
                placeholder="单个持仓最大市值"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">总仓位上限(%)</div>
              <input
                className={INPUT_CLS}
                type="number"
                value={(cfg.max_total_exposure || 0.5) * 100}
                onChange={(e) => setCfg({ ...cfg, max_total_exposure: Number(e.target.value) / 100 })}
                placeholder="总仓位上限"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">最小现金比例(%)</div>
              <input
                className={INPUT_CLS}
                type="number"
                value={(cfg.min_cash_ratio || 0.3) * 100}
                onChange={(e) => setCfg({ ...cfg, min_cash_ratio: Number(e.target.value) / 100 })}
                placeholder="最小现金比例"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">单轮同向最多新单（买）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                max={20}
                value={cfg.same_direction_max_new_orders_per_scan ?? 2}
                onChange={(e) =>
                  setCfg({ ...cfg, same_direction_max_new_orders_per_scan: Number(e.target.value) })
                }
                placeholder="默认 2"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">最多并发多头持仓数</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                max={200}
                value={cfg.max_concurrent_long_positions ?? 8}
                onChange={(e) => setCfg({ ...cfg, max_concurrent_long_positions: Number(e.target.value) })}
                placeholder="默认 8"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">同标的冷却(分钟)</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0}
                max={240}
                value={cfg.same_symbol_cooldown_minutes ?? 30}
                onChange={(e) => setCfg({ ...cfg, same_symbol_cooldown_minutes: Number(e.target.value) })}
                placeholder="同标的冷却分钟数"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">同标的日内次数上限</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                max={20}
                value={cfg.same_symbol_max_trades_per_day ?? 1}
                onChange={(e) => setCfg({ ...cfg, same_symbol_max_trades_per_day: Number(e.target.value) })}
                placeholder="同标的日内次数上限"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">同标的日内卖出上限</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                max={20}
                value={cfg.same_symbol_max_sells_per_day ?? 1}
                onChange={(e) => setCfg({ ...cfg, same_symbol_max_sells_per_day: Number(e.target.value) })}
                placeholder="同标的日内卖出上限"
              />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={cfg.avoid_add_to_existing_position !== false}
                onChange={(e) => setCfg({ ...cfg, avoid_add_to_existing_position: e.target.checked })}
              />
              已有持仓时禁止自动加仓
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={cfg.sell_full_position !== false}
                onChange={(e) => setCfg({ ...cfg, sell_full_position: e.target.checked })}
              />
              自动卖出时默认清仓
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">自动卖出股数（非清仓模式）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                value={cfg.sell_order_quantity ?? 100}
                onChange={(e) => setCfg({ ...cfg, sell_order_quantity: Number(e.target.value) })}
                placeholder="非清仓模式下每次卖出股数"
              />
            </label>
          </div>

          <div className="grid grid-cols-1 gap-2 rounded-lg border border-slate-700/70 bg-slate-900/40 p-3 md:grid-cols-4">
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">K线周期</div>
              <select
                className={INPUT_CLS}
                value={cfg.kline}
                onChange={(e) =>
                  setCfg({
                    ...cfg,
                    kline: e.target.value as AutoConfig["kline"],
                  })
                }
              >
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
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">评分回测天数</div>
              <input
                className={INPUT_CLS}
                type="number"
                value={cfg.backtest_days}
                onChange={(e) => setCfg({ ...cfg, backtest_days: Number(e.target.value) })}
                placeholder="评分回测天数"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">信号检测天数</div>
              <input
                className={INPUT_CLS}
                type="number"
                value={cfg.signal_bars_days}
                onChange={(e) => setCfg({ ...cfg, signal_bars_days: Number(e.target.value) })}
                placeholder="信号检测天数"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">每次下单数量</div>
              <input
                className={INPUT_CLS}
                type="number"
                value={cfg.order_quantity}
                onChange={(e) => setCfg({ ...cfg, order_quantity: Number(e.target.value) })}
                placeholder="每次下单数量"
              />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={!!cfg.ml_filter_enabled}
                onChange={(e) => setCfg({ ...cfg, ml_filter_enabled: e.target.checked })}
              />
              启用ML买入过滤
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">ML模型</div>
              <select
                className={INPUT_CLS}
                value={cfg.ml_model_type || "logreg"}
                onChange={(e) =>
                  setCfg({ ...cfg, ml_model_type: e.target.value as "logreg" | "random_forest" | "gbdt" })
                }
              >
                <option value="logreg">Logistic Regression</option>
                <option value="random_forest">Random Forest</option>
                <option value="gbdt">GBDT</option>
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">ML阈值</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0.5}
                max={0.95}
                step={0.01}
                value={cfg.ml_threshold ?? 0.6}
                onChange={(e) => setCfg({ ...cfg, ml_threshold: Number(e.target.value) })}
                placeholder="默认 0.60"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">ML预测周期(天)</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                max={30}
                value={cfg.ml_horizon_days ?? 5}
                onChange={(e) => setCfg({ ...cfg, ml_horizon_days: Number(e.target.value) })}
                placeholder="默认 5"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">ML训练比例</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0.5}
                max={0.9}
                step={0.05}
                value={cfg.ml_train_ratio ?? 0.7}
                onChange={(e) => setCfg({ ...cfg, ml_train_ratio: Number(e.target.value) })}
                placeholder="默认 0.7"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">ML WF窗口数</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                max={10}
                step={1}
                value={cfg.ml_walk_forward_windows ?? 4}
                onChange={(e) => setCfg({ ...cfg, ml_walk_forward_windows: Number(e.target.value) })}
                title="实盘 ML 概率估计使用的 walk-forward 段数，上限 10"
                placeholder="默认 4"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">ML缓存分钟</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0}
                max={1440}
                value={cfg.ml_filter_cache_minutes ?? 15}
                onChange={(e) => setCfg({ ...cfg, ml_filter_cache_minutes: Number(e.target.value) })}
                placeholder="默认 15"
              />
            </label>
              {!!cfg.ml_filter_enabled ? (
                <label className="space-y-1 text-sm md:col-span-4">
                  <div className="text-xs text-slate-400">ML应用到AutoTrader（覆盖当前 ML 参数）</div>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <select
                      className={INPUT_CLS}
                      style={{ maxWidth: 340 }}
                      value={mlApplySnapshotId}
                      onChange={(e) => setMlApplySnapshotId(e.target.value)}
                      title="选择要应用的 ML 矩阵快照（不选则应用最新结果）"
                      disabled={mlApplyBusy}
                    >
                      <option value="">使用最新 ML 结果</option>
                      {mlMatrixSnapshotHistory.map((s: any) => (
                        <option key={String(s?.snapshot_id || "")} value={String(s?.snapshot_id || "")}>
                          {String(s?.kline || "-").toUpperCase()} sig{String(s?.signal_bars_days_requested ?? "-")} ·{" "}
                          {s?.generated_at ? formatTime(String(s?.generated_at || "")) : "-"}
                        </option>
                      ))}
                    </select>
                    <select
                      className={INPUT_CLS}
                      style={{ maxWidth: 220 }}
                      value={mlApplyVariant}
                      onChange={(e) => setMlApplyVariant(e.target.value as MlApplyVariant)}
                      title="来自哪一条 variant"
                      disabled={mlApplyBusy}
                    >
                      <option value="auto">应用来源：自动（推荐）</option>
                      <option value="balanced">平衡 best_balanced</option>
                      <option value="high_precision">高精确 best_high_precision</option>
                      <option value="high_coverage">高覆盖 best_high_coverage</option>
                      <option value="best_score">整表最高分 best_score</option>
                    </select>
                    <button
                      className="rounded-lg bg-gradient-to-r from-amber-600 to-orange-600 px-3 py-2 text-xs font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
                      onClick={applyMlMatrixToConfig}
                      disabled={mlApplyBusy}
                      title="写入配置文件；Worker 需从配置读取更新（如独立 Worker，建议重启）"
                    >
                      {mlApplyBusy ? "应用中…" : "应用ML最优到AutoTrader"}
                    </button>
                  </div>
                  <div className="text-[11px] text-slate-500">
                    写入后将覆盖 `ml_model_type / ml_threshold / ml_horizon_days / ml_train_ratio / ml_walk_forward_windows`。
                  </div>
                </label>
              ) : null}
            <label className="flex flex-col gap-1 text-sm md:col-span-2">
              <span className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={!!cfg.research_allocation_enabled}
                  onChange={(e) => setCfg({ ...cfg, research_allocation_enabled: e.target.checked })}
                />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="font-medium text-slate-200">运用 Research 分配</span>
                  <span className="text-[11px] leading-snug text-slate-500">
                    按快照 <code className="text-cyan-400/90">allocation_plan</code> 权重裁剪买入数量
                  </span>
                </span>
              </span>
              <span className="text-xs text-slate-500">
                需先运行 Research 生成快照；买入股数 = min(原仓位模型数量, 账户权益×权重×倍数/现价)，且受单标的市值上限约束。标的不在分配表中则仍用原数量。
              </span>
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">分配快照最大有效（分钟，0=不检查）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0}
                max={10080}
                value={cfg.research_allocation_max_age_minutes ?? 0}
                onChange={(e) => setCfg({ ...cfg, research_allocation_max_age_minutes: Number(e.target.value) })}
                placeholder="0 表示永不过期"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">分配快照选择</div>
              <select
                className={INPUT_CLS}
                value={cfg.research_allocation_snapshot_id ?? ""}
                disabled={!cfg.research_allocation_enabled}
                onChange={(e) =>
                  setCfg({
                    ...cfg,
                    research_allocation_snapshot_id: e.target.value ? String(e.target.value) : undefined,
                  })
                }
              >
                <option value="">使用最新 Research 快照</option>
                {researchSnapshotHistory.map((s: any) => (
                  <option key={String(s?.snapshot_id || "")} value={String(s?.snapshot_id || "")}>
                    {String(s?.backtest_days_requested ?? "-")}
                    (使用={String(s?.backtest_days_used ?? "-")})
                    {s?.kline ? ` · ${String(s?.kline || "").toUpperCase()}` : ""}
                    {s?.generated_at ? ` · ${formatTime(String(s?.generated_at || ""))}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">分配名义倍数（0.01–3）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0.01}
                max={3}
                step={0.05}
                value={cfg.research_allocation_notional_scale ?? 1}
                onChange={(e) => setCfg({ ...cfg, research_allocation_notional_scale: Number(e.target.value) })}
                placeholder="默认 1"
              />
            </label>
            </div>
          </details>

          <details className="rounded-lg border border-slate-700/70 bg-slate-900/30 p-3" open={!compactMode}>
            <summary className="cursor-pointer list-none text-sm font-semibold tracking-wide text-slate-100">
              策略组件化配置（阶段 1-4）
              <span className="ml-2 text-xs font-normal text-slate-400">
                {compactMode ? "（默认折叠）" : "（已展开）"}
              </span>
            </summary>
            <div className="mt-3 rounded-lg border border-slate-700/70 bg-slate-900/40 p-3">
            <div className="mb-2 text-sm text-slate-300">策略模板（一键切换）</div>
            <div className="flex flex-wrap gap-2">
              {templates.map((t) => (
                <button
                  key={t.name}
                  className="rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-1.5 text-xs text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50"
                  onClick={() => applyTemplate(t.name)}
                  disabled={switchingTemplate}
                  title={t.description || ""}
                >
                  {switchingTemplate ? "切换中..." : `应用模板：${t.label}`}
                </button>
              ))}
              <button
                className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-200 hover:bg-emerald-500/20"
                onClick={exportConfig}
              >
                导出配置
              </button>
              <button
                className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-1.5 text-xs text-violet-200 hover:bg-violet-500/20"
                onClick={() => fileInputRef.current?.click()}
              >
                导入配置
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/json,.json"
                className="hidden"
                onChange={(e) => importConfig(e.target.files?.[0] || null)}
              />
            </div>
            <div className="mt-3 text-xs text-slate-400">配置备份（最近 {backups.length} 份，可一键回滚）</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {backups.slice(0, 6).map((b, idx) => (
                <button
                  key={String(b?.id || `backup-${idx}`)}
                  className="rounded border border-slate-600 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800/70"
                  onClick={() => b?.id && rollbackConfig(String(b.id))}
                  title={`${String(b?.created_at || "-")} | ${humanizeBackupReason(b?.reason)}`}
                >
                  回滚 {String(b?.id || "未知").slice(0, 10)}
                </button>
              ))}
            </div>
          </div>
            <div className="mt-3 grid grid-cols-1 gap-2 rounded-lg border border-slate-700/70 bg-slate-900/40 p-3 md:grid-cols-4">
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">入场规则 EntryRule</div>
              <select
                className={INPUT_CLS}
                value={cfg.entry_rule || "strategy_cross"}
                onChange={(e) => setCfg({ ...cfg, entry_rule: e.target.value as AutoConfig["entry_rule"] })}
              >
                <option value="strategy_cross">策略交叉信号</option>
                <option value="breakout">Breakout 突破入场</option>
                <option value="mean_reversion">Mean Reversion 均值回归入场</option>
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">Breakout 回看周期（bars）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={5}
                max={240}
                value={cfg.breakout_lookback_bars ?? 20}
                onChange={(e) => setCfg({ ...cfg, breakout_lookback_bars: Number(e.target.value) })}
                placeholder="默认 20"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">Breakout 量能阈值（倍）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0}
                step={0.1}
                value={cfg.breakout_volume_ratio ?? 1.2}
                onChange={(e) => setCfg({ ...cfg, breakout_volume_ratio: Number(e.target.value) })}
                placeholder="默认 1.2"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">均值回归 RSI 阈值</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                max={80}
                step={0.5}
                value={cfg.mean_reversion_rsi_threshold ?? 35}
                onChange={(e) => setCfg({ ...cfg, mean_reversion_rsi_threshold: Number(e.target.value) })}
                placeholder="默认 35"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">均值回归偏离阈值（%）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0}
                max={30}
                step={0.1}
                value={cfg.mean_reversion_deviation_pct ?? 2}
                onChange={(e) => setCfg({ ...cfg, mean_reversion_deviation_pct: Number(e.target.value) })}
                placeholder="默认 2%"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">仓位模型 Sizer</div>
              <select
                className={INPUT_CLS}
                value={cfg.sizer?.type || "fixed"}
                onChange={(e) =>
                  setCfg({
                    ...cfg,
                    sizer: { ...(cfg.sizer || {}), type: e.target.value as "fixed" | "risk_percent" | "volatility" },
                  })
                }
              >
                <option value="fixed">固定股数</option>
                <option value="risk_percent">净值风险比例</option>
                <option value="volatility">波动率仓位</option>
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">固定仓位股数</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                value={cfg.sizer?.quantity ?? cfg.order_quantity ?? 100}
                onChange={(e) =>
                  setCfg({
                    ...cfg,
                    sizer: { ...(cfg.sizer || {}), quantity: Number(e.target.value) },
                  })
                }
                placeholder="固定仓位股数"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">风险比例（risk_percent）%</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0.1}
                step={0.1}
                value={((cfg.sizer?.risk_pct ?? 0.01) * 100).toFixed(2)}
                onChange={(e) =>
                  setCfg({
                    ...cfg,
                    sizer: { ...(cfg.sizer || {}), risk_pct: Number(e.target.value) / 100 },
                  })
                }
                placeholder="例如 1 表示 1%"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">目标波动率（volatility）%</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0.1}
                step={0.1}
                value={((cfg.sizer?.target_vol_pct ?? 0.02) * 100).toFixed(2)}
                onChange={(e) =>
                  setCfg({
                    ...cfg,
                    sizer: { ...(cfg.sizer || {}), target_vol_pct: Number(e.target.value) / 100 },
                  })
                }
                placeholder="例如 2 表示 2%"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">硬止损阈值（%）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0.1}
                step={0.1}
                value={cfg.hard_stop_pct ?? 6}
                onChange={(e) => setCfg({ ...cfg, hard_stop_pct: Number(e.target.value) })}
                placeholder="默认 6%"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">止盈阈值（%）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0.1}
                step={0.1}
                value={cfg.take_profit_pct ?? 12}
                onChange={(e) => setCfg({ ...cfg, take_profit_pct: Number(e.target.value) })}
                placeholder="默认 12%"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">时间止损（小时）</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={1}
                value={cfg.time_stop_hours ?? 72}
                onChange={(e) => setCfg({ ...cfg, time_stop_hours: Number(e.target.value) })}
                placeholder="默认 72 小时"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">手续费(bps)</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0}
                value={cfg.cost_model?.commission_bps ?? 3}
                onChange={(e) =>
                  setCfg({
                    ...cfg,
                    cost_model: { ...(cfg.cost_model || {}), commission_bps: Number(e.target.value) },
                  })
                }
                placeholder="默认 3"
              />
            </label>
            <label className="space-y-1 text-sm">
              <div className="text-xs text-slate-400">滑点(bps)</div>
              <input
                className={INPUT_CLS}
                type="number"
                min={0}
                value={cfg.cost_model?.slippage_bps ?? 5}
                onChange={(e) =>
                  setCfg({
                    ...cfg,
                    cost_model: { ...(cfg.cost_model || {}), slippage_bps: Number(e.target.value) },
                  })
                }
                placeholder="默认 5"
              />
            </label>
          </div>

            <div className="mt-3 space-y-1 rounded-lg border border-slate-700/70 bg-slate-900/40 p-3">
            <div className="text-sm text-slate-300">出场规则 ExitRules（按优先级从上到下）</div>
            <div className="mt-1 flex flex-wrap gap-3 text-sm">
              {EXIT_RULE_OPTIONS.map((rule) => (
                <label
                  key={rule.key}
                  className={`flex items-center gap-2 rounded-full border px-3 py-1.5 transition ${
                    (cfg.exit_rules || []).includes(rule.key)
                      ? "border-rose-400/50 bg-rose-500/10 text-rose-200"
                      : "border-slate-700 bg-slate-900/60 text-slate-300"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={(cfg.exit_rules || []).includes(rule.key)}
                    onChange={() => {
                      const curr = new Set(cfg.exit_rules || []);
                      if (curr.has(rule.key)) curr.delete(rule.key);
                      else curr.add(rule.key);
                      const next = Array.from(curr);
                      setCfg({
                        ...cfg,
                        exit_rules: next,
                        rule_priority: next,
                      });
                    }}
                  />
                  <span className="text-xs">{rule.label}</span>
                </label>
              ))}
            </div>
          </div>

            <div className="mt-3 space-y-1">
            <div className="text-sm text-slate-300">参与评分策略</div>
            <p className="text-[11px] leading-snug text-slate-500">
              勾选表示参与评分；点击右侧<strong className="text-slate-400">「编辑」</strong>在弹窗中修改内参（写入{" "}
              <code className="text-cyan-400/90">strategy_params_map</code>）。留空字段表示使用策略默认。与「并入矩阵优选前三」变体
              <span className="text-slate-400">并行综合评分</span>。
            </p>
            <div className="mt-2 grid grid-cols-2 gap-2">
              {strategyCandidates.map((s) => {
                const opt = strategyOptions.find((x) => x.name === s);
                const label = opt?.label || s;
                const paramStr = formatStrategyParamsParen(s, cfg.strategy_params_map, opt?.default_params);
                const tip = [opt?.description || "", paramStr ? `当前参数: ${paramStr}` : ""].filter(Boolean).join("\n");
                const on = (cfg.strategies || []).includes(s);
                return (
                  <div
                    key={s}
                    className={`flex min-w-0 items-start gap-2 rounded-lg border px-2.5 py-2 transition ${
                      on ? "border-cyan-500/35 bg-cyan-500/[0.07]" : "border-slate-700/80 bg-slate-900/40"
                    }`}
                  >
                    <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        className="mt-0.5 shrink-0"
                        checked={on}
                        onChange={() => toggleStrategy(s)}
                      />
                      <span className="min-w-0 text-xs leading-snug" title={tip || undefined}>
                        <span className={`font-medium ${on ? "text-cyan-100" : "text-slate-200"}`}>{label}</span>
                        <span className="ml-1 font-mono text-[10px] text-slate-500">{s}</span>
                        {paramStr ? (
                          <span className="mt-0.5 block truncate text-[10px] font-normal text-slate-500">
                            {paramStr}
                          </span>
                        ) : null}
                      </span>
                    </label>
                    <button
                      type="button"
                      className="shrink-0 rounded-md border border-cyan-500/40 bg-slate-900/80 px-2.5 py-1 text-[11px] font-medium text-cyan-200 hover:border-cyan-400/70 hover:bg-cyan-500/10"
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        startEditing();
                        setStrategyParamsModal(s);
                      }}
                    >
                      编辑
                    </button>
                  </div>
                );
              })}
            </div>
            <label className="mt-3 flex flex-col gap-1 text-sm md:col-span-2">
              <span className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={!!cfg.merge_strategy_matrix_top3}
                  onChange={(e) => setCfg({ ...cfg, merge_strategy_matrix_top3: e.target.checked })}
                />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="font-medium text-slate-200">并入矩阵优选前 3 策略参与评分</span>
                  <span className="text-[11px] leading-snug text-slate-500">
                    读取当前已保存的「策略参数矩阵」结果（<code className="text-cyan-400/90">matrix_score</code> 排名），将前 3
                    条变体连同 <code className="text-cyan-400/90">strategy_params</code> 与上方勾选策略一起回测评分；需在{" "}
                    <Link className="text-cyan-300 underline hover:text-cyan-200" href="/research">
                      研究中心
                    </Link>{" "}
                    先跑完矩阵。
                  </span>
                </span>
              </span>
            </label>
            {cfg.merge_strategy_matrix_top3 ? (
              <label className="mt-2 space-y-1 text-sm md:col-span-2">
                <div className="text-xs text-slate-400">策略矩阵快照选择</div>
                <select
                  className={INPUT_CLS}
                  value={cfg.merge_strategy_matrix_top3_snapshot_id ?? ""}
                  disabled={!cfg.merge_strategy_matrix_top3}
                  onChange={(e) =>
                    setCfg({
                      ...cfg,
                      merge_strategy_matrix_top3_snapshot_id: e.target.value ? String(e.target.value) : undefined,
                    })
                  }
                >
                  <option value="">使用最新策略矩阵快照</option>
                  {strategyMatrixSnapshotHistory.map((s: any) => (
                    <option key={String(s?.snapshot_id || "")} value={String(s?.snapshot_id || "")}>
                      {String(s?.backtest_days_requested ?? "-")}
                      {s?.kline ? ` · ${String(s?.kline || "").toUpperCase()}` : ""}
                      {s?.profile_tag
                        ? ` · ${MATRIX_PROFILE_LABEL[String(s.profile_tag)] || String(s.profile_tag)}`
                        : ""}
                      {s?.generated_at ? ` · ${formatTime(String(s?.generated_at || ""))}` : ""}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>

            <div className="mt-3 space-y-1">
            <div className="text-sm text-slate-300">股票池（可编辑，逗号或换行分隔）</div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
              <div className="space-y-1">
                <div className="text-xs text-slate-400">美股 US</div>
                <textarea
                  className={`${INPUT_CLS} h-36 text-xs`}
                  value={universeInput.us}
                  onChange={(e) => setUniverseInput((x) => ({ ...x, us: e.target.value }))}
                />
              </div>
              <div className="space-y-1">
                <div className="text-xs text-slate-400">港股 HK</div>
                <textarea
                  className={`${INPUT_CLS} h-36 text-xs`}
                  value={universeInput.hk}
                  onChange={(e) => setUniverseInput((x) => ({ ...x, hk: e.target.value }))}
                />
              </div>
              <div className="space-y-1">
                <div className="text-xs text-slate-400">A股 CN</div>
                <textarea
                  className={`${INPUT_CLS} h-36 text-xs`}
                  value={universeInput.cn}
                  onChange={(e) => setUniverseInput((x) => ({ ...x, cn: e.target.value }))}
                />
              </div>
            </div>
          </div>

            <div className="mt-3 space-y-1">
            <div className="text-sm text-slate-300">ETF配对池（每行 LONG=INVERSE，例如 SPY.US=SH.US）</div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
              <div className="space-y-1">
                <div className="text-xs text-slate-400">美股 US</div>
                <textarea
                  className={`${INPUT_CLS} h-28 text-xs`}
                  value={pairInput.us}
                  onChange={(e) => setPairInput((x) => ({ ...x, us: e.target.value }))}
                />
              </div>
              <div className="space-y-1">
                <div className="text-xs text-slate-400">港股 HK</div>
                <textarea
                  className={`${INPUT_CLS} h-28 text-xs`}
                  value={pairInput.hk}
                  onChange={(e) => setPairInput((x) => ({ ...x, hk: e.target.value }))}
                />
              </div>
              <div className="space-y-1">
                <div className="text-xs text-slate-400">A股 CN</div>
                <textarea
                  className={`${INPUT_CLS} h-28 text-xs`}
                  value={pairInput.cn}
                  onChange={(e) => setPairInput((x) => ({ ...x, cn: e.target.value }))}
                />
              </div>
            </div>
          </div>

          </details>

          <div className="flex flex-wrap gap-2">
            <button
              className="rounded-lg bg-gradient-to-r from-cyan-500 to-blue-500 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
              onClick={saveConfig}
              disabled={saving}
            >
              {saving ? "保存中..." : "保存配置"}
            </button>
            <button
              className="rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
              onClick={runScan}
              disabled={busy}
            >
              {busy ? "扫描中..." : "手动扫描"}
            </button>
            <button
              className="rounded-lg bg-gradient-to-r from-emerald-500 to-teal-600 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
              onClick={restartWorker}
              disabled={saving || busy || restartingWorker || !cfg?.enabled}
              title={!cfg?.enabled ? "请先开启“启用自动扫描”" : "重启 worker（重新加载配置），不影响飞书"}
            >
              {restartingWorker ? "重启中..." : "重启 Worker"}
            </button>
          </div>
        </div>
      ) : (
        <div className="panel">加载配置中...</div>
      )}

      <div className="panel space-y-2 rounded-lg border border-indigo-500/30 bg-indigo-950/20 p-4">
        <div className={PANEL_TITLE_CLS}>研究中心</div>
        <p className="mt-1 text-sm text-slate-300">
          Research、策略矩阵与 A/B 报告已迁移至独立页面，请在侧栏进入{" "}
          <Link className="text-cyan-300 underline hover:text-cyan-200" href="/research">
            研究中心
          </Link>
          。
        </p>
      </div>

      <div className="panel space-y-2">
        <div className={PANEL_TITLE_CLS}>本轮强势股</div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span title="强势股接口返回的列表刷新时间（含 API 缓存/即时筛选）">
              列表刷新：{formatTime(scanDiagnostics?.scan_time || status?.last_scan_summary?.scan_time)}
            </span>
            <span title="与下方「决策链路」同源，仅 Worker 完整扫描后更新">
              Worker完整扫描：{formatTime(scanDiagnostics?.worker_last_scan_summary_at || status?.last_scan_summary?.scan_time)}
            </span>
            <span>worker更新时间：{formatTime(scanDiagnostics?.worker_updated_at || undefined)}</span>
            <span className="text-cyan-300">强势股数：{scanDiagnostics?.strong_count ?? liveStrongStocks.length ?? 0}</span>
            <span className="text-amber-300">评分失败：{scanDiagnostics?.score_error ?? 0}</span>
            <span>无信号：{scanDiagnostics?.no_signal ?? 0}</span>
            <span>ML过滤：{scanDiagnostics?.ml_filter ?? 0}</span>
            <span>风控拦截：{scanDiagnostics?.duplicate_guard ?? 0}</span>
          </div>
          {(status as any)?.runtime?.worker?.scan_in_progress ? (
            <div className="mt-2 text-cyan-300">
              Worker 正在执行完整扫描（开始于 {formatTime((status as any)?.runtime?.worker?.scan_started_at)}），期间按钮响应可能变慢。
            </div>
          ) : null}
          {scanDiagnostics?.scheduler_scan_in_progress ? (
            <div className="mt-2 text-amber-300" title="定时调度线程正在执行 run_scan_once；若长时间不变可能是行情/K 线请求阻塞">
              定时调度扫描进行中（开始于 {formatTime(scanDiagnostics.scheduler_scan_started_at)}）
              {scanDiagnostics.scheduler_scan_finished_at
                ? ` · 上轮结束 ${formatTime(scanDiagnostics.scheduler_scan_finished_at)}`
                : null}
            </div>
          ) : null}
          {scanDiagnostics?.scheduler_last_error ? (
            <div className="mt-2 break-all text-rose-300" title="定时调度上一轮 run_scan_once 抛错（已记录日志）">
              定时调度上次失败：{scanDiagnostics.scheduler_last_error}
            </div>
          ) : null}
          {scanDiagnostics?.last_manual_scan_error ? (
            <div className="mt-2 text-rose-300">手动扫描错误：{scanDiagnostics.last_manual_scan_error}</div>
          ) : null}
          {scanDiagnostics?.score_error_examples?.length ? (
            <div className="mt-2">
              <div className="text-amber-300">最近评分失败样本（最多5条）</div>
              <div className="mt-1 space-y-1">
                {scanDiagnostics.score_error_examples.map((row, idx) => (
                  <div key={`${row.symbol || "unknown"}-${idx}`} className="text-slate-300">
                    {row.symbol || "-"} {row.side ? `(${row.side})` : ""} - {row.reason || "score_error"}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {scanDiagnostics?.invalid_symbol_errors?.length ? (
            <div className="mt-2">
              <div className="text-amber-300">无效标的样本（最多5条）</div>
              <div className="mt-1 space-y-1">
                {scanDiagnostics.invalid_symbol_errors.map((msg, idx) => (
                  <div key={`invalid-${idx}`} className="text-slate-300">
                    {msg}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
        {liveStrongStocks.length ? (
          <div className="overflow-x-auto rounded-lg border border-slate-700/70">
            <table className="w-full min-w-[980px] text-sm">
              <thead className="bg-slate-900/70 text-left text-slate-300">
                <tr>
                  <th className="px-3 py-2">代码</th>
                  <th className="px-3 py-2">现价</th>
                  <th className="px-3 py-2">价格类型</th>
                  <th className="px-3 py-2">当日涨跌%</th>
                  <th className="px-3 py-2">5日%</th>
                  <th className="px-3 py-2">20日%</th>
                  <th className="px-3 py-2">强度分数</th>
                </tr>
              </thead>
              <tbody>
                {liveStrongStocks.map((x: any) => (
                  <tr key={x.symbol} className="border-t border-slate-800/90 hover:bg-slate-900/40">
                    <td className="px-3 py-2 font-medium text-slate-100">{x.symbol}</td>
                    <td className="px-3 py-2">{x.last ?? "-"}</td>
                    <td className="px-3 py-2 text-xs text-slate-300">{x.price_type ?? "-"}</td>
                    <td className={`px-3 py-2 ${Number(x.change_pct) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{x.change_pct ?? "-"}</td>
                    <td className={`px-3 py-2 ${Number(x.ret5_pct) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{x.ret5_pct ?? "-"}</td>
                    <td className={`px-3 py-2 ${Number(x.ret20_pct) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{x.ret20_pct ?? "-"}</td>
                    <td className="px-3 py-2 text-cyan-300">{x.strength_score ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="text-sm text-slate-400">暂无数据，先点击"手动扫描"</div>
        )}
      </div>

      {!cfg?.auto_execute && (
        <div className="panel space-y-2">
          <div className={PANEL_TITLE_CLS}>待确认信号（半自动模式）</div>
          {!signals.length ? (
            <div className="text-sm text-slate-400">当前无待确认信号</div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-slate-700/70">
              <table className="w-full min-w-[1100px] text-sm">
                <thead className="bg-slate-900/70 text-left text-slate-300">
                  <tr>
                    <th className="px-3 py-2">信号ID</th>
                    <th className="px-3 py-2">标的</th>
                    <th className="px-3 py-2">方向</th>
                    <th className="px-3 py-2">数量</th>
                    <th className="px-3 py-2">建议价</th>
                    <th className="px-3 py-2">策略</th>
                    <th className="px-3 py-2">评分</th>
                    <th className="px-3 py-2">触发原因</th>
                    <th className="px-3 py-2">创建时间</th>
                    <th className="px-3 py-2">到期时间</th>
                    <th className="px-3 py-2">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {signals.map((s) => {
                    const stratPs = formatStrategyParamsRecord(signalStrategyParams(s));
                    return (
                      <tr key={s.signal_id} className="border-t border-slate-800/90 hover:bg-slate-900/40">
                        <td className="px-3 py-2 font-medium text-slate-100">{s.signal_id}</td>
                        <td className="px-3 py-2">{s.symbol}</td>
                        <td className="px-3 py-2">{s.action === "buy" ? "买入" : s.action === "sell" ? "卖出" : s.action}</td>
                        <td className="px-3 py-2">{s.quantity}</td>
                        <td className="px-3 py-2">{s.suggested_price ?? "-"}</td>
                        <td className="max-w-[220px] px-3 py-2">
                          <div className="text-sm text-slate-200">{s.strategy_label || s.strategy || "-"}</div>
                          {stratPs ? (
                            <div className="mt-0.5 break-words text-[10px] leading-snug text-slate-500" title={stratPs}>
                              ({stratPs})
                            </div>
                          ) : null}
                        </td>
                        <td className="px-3 py-2 text-cyan-300">{s.strategy_score ?? "-"}</td>
                        <td className="px-3 py-2 text-xs text-slate-300" title={safeStringify(s.trace || {})}>
                          {humanizeReason(s.reason)}
                        </td>
                        <td className="px-3 py-2">{formatTime(s.created_at)}</td>
                        <td className="px-3 py-2">{formatTime(s.expires_at)}</td>
                        <td className="px-3 py-2">
                          <button
                            className="rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 px-2.5 py-1.5 text-xs text-white shadow hover:opacity-90 disabled:opacity-50"
                            onClick={() => confirmSignal(s.signal_id)}
                            disabled={!!confirming[s.signal_id]}
                          >
                            {confirming[s.signal_id] ? "确认中..." : "确认下单"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {executedSignals.length > 0 && (
        <div className="panel space-y-2">
          <div className={PANEL_TITLE_CLS}>已执行信号</div>
          <div className="overflow-x-auto rounded-lg border border-slate-700/70">
            <table className="w-full min-w-[1050px] text-sm">
              <thead className="bg-slate-900/70 text-left text-slate-300">
                <tr>
                  <th className="px-3 py-2">信号ID</th>
                  <th className="px-3 py-2">标的</th>
                  <th className="px-3 py-2">方向</th>
                  <th className="px-3 py-2">数量</th>
                  <th className="px-3 py-2">策略</th>
                  <th className="px-3 py-2">评分</th>
                  <th className="px-3 py-2">触发原因</th>
                  <th className="px-3 py-2">执行时间</th>
                  <th className="px-3 py-2">模式</th>
                  <th className="px-3 py-2">order_id</th>
                  <th className="px-3 py-2">实际下单结果</th>
                </tr>
              </thead>
              <tbody>
                {executedSignals.slice(0, 10).map((s) => {
                  const stratPs = formatStrategyParamsRecord(signalStrategyParams(s));
                  const exec = (s as any)?.execution_result;
                  const nestedSuccess = exec?.trade_record?.result?.success;
                  const topSuccess = exec?.success;
                  const success =
                    typeof nestedSuccess === "boolean"
                      ? nestedSuccess
                      : typeof topSuccess === "boolean"
                        ? topSuccess
                        : undefined;
                  const orderId =
                    exec?.trade_record?.result?.order_id ?? exec?.trade_record?.order_id ?? exec?.order_id ?? "-";
                  const failReason =
                    success === false
                      ? exec?.trade_record?.result?.error || exec?.error || exec?.trade_record?.error || ""
                      : "";
                  const failReasonText = typeof failReason === "string" && failReason.trim() ? failReason : "";
                  return (
                    <tr key={s.signal_id} className="border-t border-slate-800/90 hover:bg-slate-900/40">
                      <td className="px-3 py-2 font-medium text-slate-100">{s.signal_id}</td>
                      <td className="px-3 py-2">{s.symbol}</td>
                      <td className="px-3 py-2">{s.action === "buy" ? "买入" : s.action === "sell" ? "卖出" : s.action}</td>
                      <td className="px-3 py-2">{s.quantity}</td>
                      <td className="max-w-[220px] px-3 py-2">
                        <div className="text-sm text-slate-200">{s.strategy_label || s.strategy || "-"}</div>
                        {stratPs ? (
                          <div className="mt-0.5 break-words text-[10px] leading-snug text-slate-500" title={stratPs}>
                            ({stratPs})
                          </div>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 text-cyan-300">{s.strategy_score ?? "-"}</td>
                      <td className="px-3 py-2 text-xs text-slate-300" title={safeStringify(s.trace || {})}>
                        {humanizeReason(s.reason)}
                      </td>
                      <td className="px-3 py-2">{formatTime(s.created_at)}</td>
                      <td className="px-3 py-2">
                        {s.status === "simulated" ? (
                          <span className="text-fuchsia-300">演练</span>
                        ) : s.auto_executed ? (
                          <span className="text-amber-400">自动</span>
                        ) : (
                          <span className="text-emerald-400">手动</span>
                        )}
                      </td>
                      <td className="px-3 py-2">{orderId}</td>
                      <td className="px-3 py-2">
                        {s.status === "simulated" ? (
                          <span className="text-fuchsia-300">failed</span>
                        ) : success === true ? (
                          <span className="text-emerald-300">success</span>
                        ) : success === false ? (
                          <span className="text-rose-300" title={failReasonText || undefined}>
                            failed
                          </span>
                        ) : (
                          <span className="text-slate-300">unknown</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {cfg && strategyParamsModal ? (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/65 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="strategy-params-modal-title"
          onClick={() => setStrategyParamsModal(null)}
        >
          <div
            className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-xl border border-slate-600/90 bg-slate-950 p-4 shadow-2xl shadow-black/50"
            onClick={(e) => e.stopPropagation()}
          >
            {(() => {
              const sn = strategyParamsModal;
              const opt = strategyOptions.find((x) => x.name === sn);
              const label = opt?.label || sn;
              const keys = paramKeysForStrategy(sn);
              return (
                <>
                  <div className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-700/80 pb-3">
                    <div>
                      <h2 id="strategy-params-modal-title" className="text-sm font-semibold text-slate-100">
                        自定义参数 · {label}
                      </h2>
                      <p className="mt-0.5 font-mono text-[11px] text-slate-500">{sn}</p>
                    </div>
                    <button
                      type="button"
                      className="rounded-lg border border-slate-600 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-800"
                      onClick={() => setStrategyParamsModal(null)}
                    >
                      关闭
                    </button>
                  </div>
                  <p className="mt-3 text-[11px] leading-snug text-slate-500">
                    修改会立即写入当前配置（保存配置后落盘）。留空表示使用策略默认。
                  </p>
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                    <button
                      type="button"
                      className="rounded border border-amber-600/50 px-2 py-1 text-[11px] text-amber-200/90 hover:bg-amber-500/10"
                      onClick={() => {
                        resetStrategyParams(sn);
                      }}
                    >
                      恢复默认
                    </button>
                  </div>
                  {keys.length === 0 ? (
                    <p className="mt-4 text-sm text-slate-500">暂无参数字段定义（使用代码内默认）。</p>
                  ) : (
                    <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {keys.map((pk) => (
                        <label key={pk} className="flex flex-col gap-1 text-xs text-slate-400">
                          <span className="text-slate-500">{pk}</span>
                          <input
                            type="number"
                            step="any"
                            className={`${INPUT_CLS} py-2 text-sm`}
                            value={displayParamInputValue(sn, pk)}
                            placeholder="默认"
                            onChange={(e) => patchStrategyParam(sn, pk, e.target.value)}
                          />
                        </label>
                      ))}
                    </div>
                  )}
                  <div className="mt-5 flex justify-end border-t border-slate-700/80 pt-3">
                    <button
                      type="button"
                      className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-500"
                      onClick={() => setStrategyParamsModal(null)}
                    >
                      完成
                    </button>
                  </div>
                </>
              );
            })()}
          </div>
        </div>
      ) : null}
    </PageShell>
  );
}
