const STORAGE_KEY = "longport.dashboard.summary.v1";

export type DashboardSummaryCache = {
  markets: { cn_hk: unknown[]; us: unknown[] };
  analysis: Record<string, unknown>;
  sector_data_source: string;
  sector_data_source_label: string;
  sector_age_seconds?: number;
  sector_last_refresh_ts?: string;
  sector_top3: unknown[];
  sector_bottom3: unknown[];
};

export function isDashboardSummaryUsable(s: unknown): s is DashboardSummaryCache {
  if (!s || typeof s !== "object") return false;
  const o = s as Record<string, unknown>;
  if (!o.analysis || typeof o.analysis !== "object") return false;
  if (!o.markets || typeof o.markets !== "object") return false;
  const m = o.markets as Record<string, unknown>;
  if (!Array.isArray(m.cn_hk) || !Array.isArray(m.us)) return false;
  if (!Array.isArray(o.sector_top3) || !Array.isArray(o.sector_bottom3)) return false;
  return true;
}

/** 后端在子任务超时时仍返回 200，但 analysis 为占位（见 api/runtime_bridge dashboard_summary） */
export function isDashboardAnalysisDegraded(analysis: unknown): boolean {
  if (!analysis || typeof analysis !== "object") return true;
  const a = analysis as Record<string, unknown>;
  if (a.data_source === "fallback") return true;
  if (a.market_environment === "数据刷新中") return true;
  return false;
}

export function isDashboardSummaryDegraded(s: unknown): boolean {
  if (!s || typeof s !== "object") return true;
  return isDashboardAnalysisDegraded((s as Record<string, unknown>).analysis);
}

/** 结构完整且非超时占位，才写入本地缓存 */
export function isDashboardSummaryPersistable(s: unknown): boolean {
  return isDashboardSummaryUsable(s) && !isDashboardSummaryDegraded(s);
}

function isFearGreedSlotGood(slot: unknown): boolean {
  if (!slot || typeof slot !== "object") return false;
  const o = slot as Record<string, unknown>;
  const comp = o.components;
  if (comp && typeof comp === "object" && (comp as Record<string, unknown>).note === "fallback") return false;
  const v = o.value;
  return typeof v === "number" && Number.isFinite(v) && v > 0;
}

function isMacroIndicatorSlotGood(slot: unknown): boolean {
  if (!slot || typeof slot !== "object") return false;
  const o = slot as Record<string, unknown>;
  if (o.interpretation === "fallback") return false;
  const v = o.value;
  return typeof v === "number" && Number.isFinite(v) && v > 0;
}

function isFearGreedSlotBad(slot: unknown): boolean {
  return !isFearGreedSlotGood(slot);
}

function isMacroSlotBad(slot: unknown): boolean {
  return !isMacroIndicatorSlotGood(slot);
}

/**
 * 本次接口中宏观指标为 0 或 interpretation/components 标记 fallback 时，
 * 从上一份可用 summary 补齐对应字段（仅替换单项，其余仍用 preferred）。
 */
export function mergeDashboardMacroIndicators(
  preferred: DashboardSummaryCache,
  fallback: DashboardSummaryCache
): { merged: DashboardSummaryCache; usedFallbackKeys: string[] } {
  const merged = JSON.parse(JSON.stringify(preferred)) as DashboardSummaryCache;
  const usedFallbackKeys: string[] = [];
  const pAnalysis = merged.analysis as Record<string, unknown>;
  const pInd = pAnalysis.indicators as Record<string, unknown> | undefined;
  const fAnalysis = fallback.analysis as Record<string, unknown>;
  const fInd = fAnalysis.indicators as Record<string, unknown> | undefined;
  if (!pInd || !fInd || typeof pInd !== "object" || typeof fInd !== "object") {
    return { merged, usedFallbackKeys };
  }

  const take = (key: string, bad: (s: unknown) => boolean, good: (s: unknown) => boolean) => {
    const cur = pInd[key];
    const old = fInd[key];
    if (bad(cur) && good(old)) {
      pInd[key] = JSON.parse(JSON.stringify(old)) as unknown;
      usedFallbackKeys.push(key);
    }
  };

  take("fear_greed_index", isFearGreedSlotBad, isFearGreedSlotGood);
  take("vix", isMacroSlotBad, isMacroIndicatorSlotGood);
  take("treasury_10y", isMacroSlotBad, isMacroIndicatorSlotGood);
  take("dollar_index", isMacroSlotBad, isMacroIndicatorSlotGood);

  return { merged, usedFallbackKeys };
}

export function readDashboardSummaryCache(): DashboardSummaryCache | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!isDashboardSummaryUsable(parsed)) return null;
    if (isDashboardSummaryDegraded(parsed)) {
      try {
        window.localStorage.removeItem(STORAGE_KEY);
      } catch {
        /* ignore */
      }
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function writeDashboardSummaryCache(s: DashboardSummaryCache): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    // ignore quota / private mode
  }
}
