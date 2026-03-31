"use client";

import { useMemo, useState } from "react";
import { apiGet } from "@/lib/api";
import { StatCard } from "@/components/stat-card";
import { MarketTable } from "@/components/market-table";
import { PageShell } from "@/components/ui/page-shell";
import {
  isDashboardSummaryDegraded,
  isDashboardSummaryPersistable,
  isDashboardSummaryUsable,
  mergeDashboardMacroIndicators,
  readDashboardSummaryCache,
  writeDashboardSummaryCache,
  type DashboardSummaryCache,
} from "@/lib/dashboard-summary-cache";
import { buildSwrOptions, SWR_INTERVALS, visibilityAwareInterval } from "@/lib/swr-config";
import useSWR from "swr";

const MACRO_MERGE_LABEL: Record<string, string> = {
  fear_greed_index: "情绪指数",
  vix: "VIX",
  treasury_10y: "10Y国债",
  dollar_index: "美元指数",
};

type Summary = {
  markets: { cn_hk: any[]; us: any[] };
  analysis: any;
  sector_data_source: string;
  sector_data_source_label: string;
  sector_age_seconds?: number;
  sector_last_refresh_ts?: string;
  sector_top3: any[];
  sector_bottom3: any[];
};

export default function DashboardPage() {
  const [persisted, setPersisted] = useState<Summary | null>(() => {
    if (typeof window === "undefined") return null;
    const c = readDashboardSummaryCache();
    return c ? (c as Summary) : null;
  });

  const { data, error, isLoading } = useSWR<Summary>(
    "/dashboard/summary",
    (path: string) => apiGet<Summary>(path, { timeoutMs: 25000, retries: 2 }),
    {
      ...buildSwrOptions(
        visibilityAwareInterval(SWR_INTERVALS.dashboardPage.refreshInterval),
        SWR_INTERVALS.dashboardPage.dedupingInterval
      ),
      onSuccess: (d) => {
        if (!isDashboardSummaryPersistable(d)) return;
        const prev = readDashboardSummaryCache();
        let toPersist = d as DashboardSummaryCache;
        if (
          prev &&
          isDashboardSummaryUsable(prev) &&
          !isDashboardSummaryDegraded(prev)
        ) {
          toPersist = mergeDashboardMacroIndicators(toPersist, prev).merged;
        }
        writeDashboardSummaryCache(toPersist);
        setPersisted(toPersist as Summary);
      },
    }
  );

  const { display, showingPersisted, macroMergedKeys } = useMemo(() => {
    const liveStructOk = data != null && isDashboardSummaryUsable(data);
    const liveGood = liveStructOk && !isDashboardSummaryDegraded(data);
    const cacheGood =
      persisted != null && isDashboardSummaryUsable(persisted) && !isDashboardSummaryDegraded(persisted);

    let displayBase: Summary | undefined;
    let showingPersistedFlag = false;

    if (liveGood) displayBase = data!;
    else if (cacheGood) {
      displayBase = persisted!;
      showingPersistedFlag = true;
    } else if (liveStructOk) displayBase = data!;
    else if (persisted != null && isDashboardSummaryUsable(persisted)) {
      displayBase = persisted;
      showingPersistedFlag = true;
    } else displayBase = data ?? persisted ?? undefined;

    let macroMergedKeys: string[] = [];
    if (
      displayBase &&
      persisted &&
      isDashboardSummaryUsable(persisted) &&
      !isDashboardSummaryDegraded(persisted)
    ) {
      const { merged, usedFallbackKeys } = mergeDashboardMacroIndicators(
        displayBase as DashboardSummaryCache,
        persisted as DashboardSummaryCache
      );
      if (usedFallbackKeys.length) {
        displayBase = merged as Summary;
        macroMergedKeys = usedFallbackKeys;
      }
    }

    return {
      display: displayBase,
      showingPersisted: showingPersistedFlag,
      macroMergedKeys,
    };
  }, [data, persisted]);

  const sourceText = () => {
    const label = display?.sector_data_source_label || "未知";
    if (display?.sector_data_source === "cache" && typeof display?.sector_age_seconds === "number") {
      return `${label}（距上次刷新 ${display.sector_age_seconds} 秒）`;
    }
    return label;
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">总览 Dashboard</h1>
            <div className="mt-1 text-sm text-slate-300">跨市场监控 · 风险评估 · 板块轮动</div>
          </div>
          <span className="tag-muted">板块数据来源：{sourceText()}</span>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="metric-card">
            <div className="field-label">风险评分</div>
            <div className="mt-1 text-xl font-semibold text-rose-500">{display?.analysis?.score ?? "-"}/5</div>
          </div>
          <div className="metric-card">
            <div className="field-label">市场情绪</div>
            <div className="mt-1 text-xl font-semibold text-slate-800">{display?.analysis?.indicators?.fear_greed_index?.value ?? "-"}</div>
          </div>
          <div className="metric-card">
            <div className="field-label">VIX 波动率</div>
            <div className="mt-1 text-xl font-semibold text-rose-500">{display?.analysis?.indicators?.vix?.value ?? "-"}</div>
          </div>
          <div className="metric-card">
            <div className="field-label">10Y 国债</div>
            <div className="mt-1 text-xl font-semibold text-slate-800">
              {display?.analysis?.indicators?.treasury_10y?.value ?? "-"}%
            </div>
          </div>
        </div>
      </div>
      {showingPersisted || macroMergedKeys.length > 0 ? (
        <div className="panel border-amber-200 bg-amber-50 text-amber-800">
          {showingPersisted ? (
            <>
              <div className="font-medium">当前展示为本地缓存（最近一次成功加载的数据）。</div>
              {error ? (
                <div className="mt-1 text-sm text-amber-700">
                  刷新失败：{String((error as Error)?.message || error)}
                </div>
              ) : data != null && isDashboardSummaryUsable(data) && isDashboardSummaryDegraded(data) ? (
                <div className="mt-1 text-sm text-amber-700">
                  本次接口返回为占位数据（如分析超时），已改用最近一次完整缓存。
                </div>
              ) : data != null && !isDashboardSummaryUsable(data) ? (
                <div className="mt-1 text-sm text-amber-700">本次返回数据不完整，已回退到缓存。</div>
              ) : null}
            </>
          ) : null}
          {!showingPersisted && macroMergedKeys.length > 0 ? (
            <div className="font-medium">
              部分宏观指标（
              {macroMergedKeys.map((k) => MACRO_MERGE_LABEL[k] || k).join("、")}
              ）本次为 0 或 fallback，已沿用上期有效数值；其余为最新接口数据。
            </div>
          ) : null}
        </div>
      ) : null}
      {display ? (
        <>
          <div className="page-header">
            <h2 className="section-title">关键指标</h2>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
            <StatCard title="风险评分" value={`${display.analysis.score}/5`} sub={display.analysis.market_environment} />
            <StatCard title="情绪指数" value={display.analysis.indicators?.fear_greed_index?.value ?? "-"} />
            <StatCard title="VIX" value={display.analysis.indicators?.vix?.value ?? "-"} />
            <StatCard title="10Y 国债" value={`${display.analysis.indicators?.treasury_10y?.value ?? "-"}%`} />
          </div>
          <div className="page-header">
            <h2 className="section-title">跨市场行情</h2>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <MarketTable title="A股/港股" rows={display.markets.cn_hk || []} />
            <MarketTable title="美股" rows={display.markets.us || []} />
          </div>
          <div className="page-header">
            <h2 className="section-title">板块轮动</h2>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="panel">
              <div className="mb-2 text-sm font-semibold text-slate-800">板块强势 Top3</div>
              {(display.sector_top3 || []).map((x) => (
                <div key={x.symbol} className="rounded-md px-2 py-1 text-sm text-emerald-600 hover:bg-blue-50">
                  {x.name} ({x.change_pct >= 0 ? "+" : ""}
                  {x.change_pct}%)
                </div>
              ))}
            </div>
            <div className="panel">
              <div className="mb-2 text-sm font-semibold text-slate-800">板块弱势 Top3</div>
              {(display.sector_bottom3 || []).map((x) => (
                <div key={x.symbol} className="rounded-md px-2 py-1 text-sm text-rose-600 hover:bg-blue-50">
                  {x.name} ({x.change_pct >= 0 ? "+" : ""}
                  {x.change_pct}%)
                </div>
              ))}
            </div>
          </div>
        </>
      ) : isLoading ? (
        <div className="panel">加载中...</div>
      ) : (
        <div className="panel">暂无数据</div>
      )}
    </PageShell>
  );
}
