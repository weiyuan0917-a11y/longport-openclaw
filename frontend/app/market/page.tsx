"use client";

import useSWR from "swr";
import { apiGet } from "@/lib/api";
import { PageShell } from "@/components/ui/page-shell";
import { buildSwrOptions, SWR_INTERVALS, visibilityAwareInterval } from "@/lib/swr-config";

export default function MarketPage() {
  const {
    data: analysis,
    error: analysisError,
    isLoading: analysisLoading,
  } = useSWR<any>(
    "/market/analysis",
    (path: string) => apiGet<any>(path, { timeoutMs: 25000, retries: 2 }),
    buildSwrOptions(
      visibilityAwareInterval(SWR_INTERVALS.marketAnalysisPage.refreshInterval),
      SWR_INTERVALS.marketAnalysisPage.dedupingInterval
    )
  );
  const {
    data: sectors,
    error: sectorsError,
    isLoading: sectorsLoading,
  } = useSWR<any>(
    "/market/sectors?days=5",
    (path: string) => apiGet<any>(path, { timeoutMs: 15000, retries: 1 }),
    buildSwrOptions(
      visibilityAwareInterval(SWR_INTERVALS.marketAnalysisPage.refreshInterval),
      SWR_INTERVALS.marketAnalysisPage.dedupingInterval
    )
  );
  const error = analysisError || sectorsError;

  const sourceText = () => {
    const label = sectors?.data_source_label || "未知";
    if (sectors?.data_source === "cache" && typeof sectors?.age_seconds === "number") {
      return `${label}（距上次刷新 ${sectors.age_seconds} 秒）`;
    }
    return label;
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">市场分析</h1>
            <div className="mt-1 text-sm text-slate-300">宏观环境 · 板块强弱 · 实时状态跟踪</div>
          </div>
          <span className="tag-muted">板块数据来源：{sourceText()}</span>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="metric-card">
            <div className="field-label">综合评分</div>
            <div className="mt-1 text-xl font-semibold text-rose-500">{analysis?.score ?? "-"}/5</div>
          </div>
          <div className="metric-card">
            <div className="field-label">情绪指数</div>
            <div className="mt-1 text-xl font-semibold text-slate-800">{analysis?.indicators?.fear_greed_index?.value ?? "-"}</div>
          </div>
          <div className="metric-card">
            <div className="field-label">新闻情绪</div>
            <div className="mt-1 text-xl font-semibold text-slate-800">{analysis?.indicators?.news_sentiment?.level ?? "-"}</div>
          </div>
          <div className="metric-card">
            <div className="field-label">风险温度</div>
            <div className="mt-1 text-xl font-semibold text-slate-800">
              {analysis?.indicators?.crypto_risk?.avg_change_24h ?? "-"}%
            </div>
          </div>
        </div>
      </div>
      {error ? (
        <div className="panel border-amber-200 bg-amber-50 text-amber-700">
          数据刷新较慢，展示上次结果：{String((error as any)?.message || error)}
        </div>
      ) : null}
      {analysisLoading && !analysis ? <div className="panel">市场分析加载中...</div> : null}
      {sectorsLoading && !sectors ? <div className="panel">板块数据加载中...</div> : null}
      <div className="panel">
        <div className="field-label">综合环境</div>
        <div className="mt-2 text-xl font-semibold text-slate-900">{analysis?.market_environment ?? "-"}</div>
        <div className="mt-1 text-sm text-slate-500">评分: {analysis?.score ?? "-"}/5</div>
        <div className="mt-2 rounded-lg border border-slate-200 bg-blue-50 px-3 py-2 text-sm text-black">
          {analysis?.strategy_recommendation ?? "-"}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="panel">
          <div className="section-title mb-2">板块强势 Top3</div>
          {(sectors?.top_performers || []).slice(0, 3).map((x: any) => (
            <div key={x.symbol} className="rounded-md px-2 py-1 text-emerald-600 hover:bg-blue-50">
              {x.name} ({x.change_pct >= 0 ? "+" : ""}
              {x.change_pct}%)
            </div>
          ))}
        </div>
        <div className="panel">
          <div className="section-title mb-2">板块弱势 Top3</div>
          {(sectors?.bottom_performers || []).slice(0, 3).map((x: any) => (
            <div key={x.symbol} className="rounded-md px-2 py-1 text-rose-600 hover:bg-blue-50">
              {x.name} ({x.change_pct >= 0 ? "+" : ""}
              {x.change_pct}%)
            </div>
          ))}
        </div>
      </div>
    </PageShell>
  );
}
