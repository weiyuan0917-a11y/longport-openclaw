from pathlib import Path

root = Path(__file__).resolve().parent
at = root.parent.parent / "app/auto-trader/page.tsx"
lines = at.read_text(encoding="utf-8").splitlines(keepends=True)
handlers = "".join(lines[999:1342]).replace("await load(true);", "await loadResearch(true);").replace(
    "await load(true)", "await loadResearch(true)"
)
computed = "".join(lines[1369:1468])
body = (root / "_panel-body.tsx.inc").read_text(encoding="utf-8")


header = '''"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
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

export function ResearchPanel() {
  const [error, setError] = useState("");
  const loadingRef = useRef(false);
  const [cfg, setCfg] = useState<AtCfgSlice | null>(null);
  const [researchStatus, setResearchStatus] = useState<ResearchStatus | null>(null);
  const [researchSnapshot, setResearchSnapshot] = useState<ResearchSnapshot | null>(null);
  const [modelCompare, setModelCompare] = useState<ModelCompareResult | null>(null);
  const [strategyMatrix, setStrategyMatrix] = useState<StrategyMatrixPayload | null>(null);
  const [mlMatrix, setMlMatrix] = useState<MlMatrixPayload | null>(null);
  const [researchRunning, setResearchRunning] = useState(false);
  const [strategyMatrixRunning, setStrategyMatrixRunning] = useState(false);
  const [mlMatrixRunning, setMlMatrixRunning] = useState(false);
  const [researchTaskId, setResearchTaskId] = useState<string>("");
  const [strategyMatrixTaskId, setStrategyMatrixTaskId] = useState<string>("");
  const [mlMatrixTaskId, setMlMatrixTaskId] = useState<string>("");
  const [abMarkdown, setAbMarkdown] = useState<string>("");
  const [pairTradeFilterPair, setPairTradeFilterPair] = useState("");
  const [pairTradeFilterSymbol, setPairTradeFilterSymbol] = useState("");

  const loadResearch = useCallback(
    async (force = false) => {
      if ((loadingRef.current && !force) || (!force && typeof document !== "undefined" && document.hidden)) {
        return;
      }
      loadingRef.current = true;
      try {
        const st = await apiGet<any>("/auto-trader/status");
        const c = st?.config;
        if (c) {
          setCfg({
            market: (c.market as AtCfgSlice["market"]) || "us",
            kline: (c.kline as AtCfgSlice["kline"]) || "1d",
            top_n: Number(c.top_n) || 8,
            backtest_days: Number(c.backtest_days) || 120,
            signal_bars_days: Number(c.signal_bars_days) || 90,
          });
        } else {
          setCfg(null);
        }
        const heavyTaskRunning = strategyMatrixRunning || mlMatrixRunning || researchRunning;
        const mkt = c ? String(c.market || "us") : "us";
        try {
          const [rs, snap, mc, sm, mm] = await Promise.all([
            apiGet<ResearchStatus>("/auto-trader/research/status"),
            apiGet<ResearchSnapshot>("/auto-trader/research/snapshot"),
            apiGet<ModelCompareResult>("/auto-trader/research/model-compare?top=10"),
            apiGet<StrategyMatrixResult>(
              `/auto-trader/research/strategy-matrix/result?market=${encodeURIComponent(mkt)}`
            ),
            apiGet<MlMatrixResult>(`/auto-trader/research/ml-matrix/result?market=${encodeURIComponent(mkt)}`),
          ]);
          setResearchStatus(rs || null);
          setResearchSnapshot(snap || null);
          setModelCompare(mc || null);
          setStrategyMatrix(sm?.result || null);
          setMlMatrix(mm?.result || null);
        } catch {
          setResearchStatus(null);
          setResearchSnapshot(null);
          setModelCompare(null);
          setStrategyMatrix(null);
          setMlMatrix(null);
        }
        if (!heavyTaskRunning) {
          try {
            const md = await apiGet<FactorABMarkdownResult>("/auto-trader/research/ab-report/markdown");
            setAbMarkdown(String(md?.markdown || ""));
          } catch {
            setAbMarkdown("");
          }
        } else {
          setAbMarkdown("");
        }
        setError("");
      } catch (e: any) {
        setError(String(e.message || e));
      } finally {
        loadingRef.current = false;
      }
    },
    [researchRunning, strategyMatrixRunning, mlMatrixRunning]
  );

  useEffect(() => {
    void loadResearch(true);
    const intervalMs = strategyMatrixRunning || mlMatrixRunning || researchRunning ? 60000 : 15000;
    const t = setInterval(() => void loadResearch(false), intervalMs);
    return () => clearInterval(t);
  }, [loadResearch, strategyMatrixRunning, mlMatrixRunning, researchRunning]);

'''

footer = (
    """  return (
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
"""
    + body
    + """
    </div>
  );
}
"""
)

# Handlers/computed 在 page.tsx 中已是组件内 2 空格缩进，不再额外 indent
out = header + handlers + computed + footer
(root / "research-panel.tsx").write_text(out, encoding="utf-8")
print("Wrote research-panel.tsx", len(out))
