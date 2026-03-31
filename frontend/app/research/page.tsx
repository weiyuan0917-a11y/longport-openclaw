"use client";

import { ResearchPanel } from "@/components/research/research-panel";
import { PageShell } from "@/components/ui/page-shell";

export default function ResearchPage() {
  return (
    <PageShell>
      <div className="panel border-indigo-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <h1 className="text-2xl font-bold tracking-tight text-white">研究中心</h1>
        <p className="mt-1 text-sm text-slate-300">
          Research、策略矩阵、ML 矩阵与 A/B 报告；参数与 Auto Trader 保存的配置一致。
        </p>
      </div>
      <ResearchPanel />
    </PageShell>
  );
}
