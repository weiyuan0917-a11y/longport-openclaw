"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { apiGet } from "@/lib/api";
import { PageShell } from "@/components/ui/page-shell";
import { buildSwrOptions, SWR_INTERVALS } from "@/lib/swr-config";
import useSWR from "swr";

const SYMBOLS_STORAGE_KEY = "signals_page_symbols_v1";
const DEFAULT_SYMBOLS = ["RXRX.US", "HOOD.US", "TSLA.US", "AAPL.US", "NVDA.US"];
const MIN_SYMBOLS = 5;
const MAX_SYMBOLS = 20;

const normalizeSymbols = (arr: unknown): string[] => {
  if (!Array.isArray(arr)) return [...DEFAULT_SYMBOLS];
  const cleaned = arr
    .slice(0, MAX_SYMBOLS)
    .map((x) => String(x ?? "").trim().toUpperCase())
    .filter(Boolean);
  while (cleaned.length < MIN_SYMBOLS) cleaned.push(DEFAULT_SYMBOLS[cleaned.length] || "");
  return cleaned;
};

export default function SignalsPage() {
  const [symbols, setSymbols] = useState<string[]>([...DEFAULT_SYMBOLS]);
  const symbolsHydratedRef = useRef(false);

  const swrKey = useMemo(
    () => `/signals/batch?symbols=${normalizeSymbols(symbols).map((s) => s.trim()).join(",")}`,
    [symbols]
  );
  const { data, error: swrError, mutate } = useSWR(
    swrKey,
    async () => {
      const normalized = normalizeSymbols(symbols).map((s) => s.trim()).filter(Boolean);
      const reqs = normalized.map((s) => apiGet<any>(`/signals?symbol=${encodeURIComponent(s)}`));
      const results = await Promise.allSettled(reqs);
      const rows: any[] = [];
      const failed: string[] = [];
      results.forEach((r, idx) => {
        if (r.status === "fulfilled") rows.push(r.value);
        else failed.push(normalized[idx] || "");
      });
      return { rows, failed: failed.filter(Boolean) };
    },
    buildSwrOptions(SWR_INTERVALS.standardPage.refreshInterval, SWR_INTERVALS.standardPage.dedupingInterval)
  );
  const error = swrError
    ? String((swrError as any)?.message || swrError)
    : data?.failed?.length
      ? `以下标的获取失败：${data.failed.join("、")}`
      : "";

  useEffect(() => {
    try {
      const raw = localStorage.getItem(SYMBOLS_STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        setSymbols(normalizeSymbols(parsed));
      }
    } catch {
      // ignore parse/storage errors
    } finally {
      symbolsHydratedRef.current = true;
    }
  }, []);

  useEffect(() => {
    if (!symbolsHydratedRef.current) return;
    try {
      localStorage.setItem(SYMBOLS_STORAGE_KEY, JSON.stringify(normalizeSymbols(symbols)));
    } catch {
      // ignore storage errors
    }
  }, [symbols]);

  const setSymbolAt = (idx: number, value: string) => {
    setSymbols((prev) => {
      const next = [...prev];
      next[idx] = value.toUpperCase();
      return next;
    });
  };

  const addSymbol = () => {
    setSymbols((prev) => {
      if (prev.length >= MAX_SYMBOLS) return prev;
      return [...prev, ""];
    });
  };

  const removeSymbolAt = (idx: number) => {
    setSymbols((prev) => {
      if (prev.length <= MIN_SYMBOLS) return prev;
      return prev.filter((_, i) => i !== idx);
    });
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">信号中心</h1>
            <div className="mt-1 text-sm text-slate-300">多标的信号监控 · 指标快照 · 触发状态追踪</div>
          </div>
          <span className="tag-muted">标的数量 {symbols.length}</span>
        </div>
      </div>
      <div className="panel space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span>当前标的数量：{symbols.length}</span>
          <span>（最少 {MIN_SYMBOLS} 个，最多 {MAX_SYMBOLS} 个）</span>
        </div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
          {symbols.map((sym, idx) => (
            <div key={`symbol-${idx}`} className="rounded-lg border border-slate-200 bg-slate-50 p-2">
              <label className="space-y-1">
                <div className="field-label">标的 {idx + 1}</div>
                <input
                  className="input-base"
                  value={sym}
                  onChange={(e) => setSymbolAt(idx, e.target.value)}
                />
              </label>
              <div className="mt-2">
                <button
                  className="btn-secondary"
                  onClick={() => removeSymbolAt(idx)}
                  disabled={symbols.length <= MIN_SYMBOLS}
                  title={symbols.length <= MIN_SYMBOLS ? `至少保留 ${MIN_SYMBOLS} 个标的` : "删除该标的"}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={addSymbol} disabled={symbols.length >= MAX_SYMBOLS}>
            添加标的
          </button>
          <button className="btn-secondary" onClick={() => mutate()}>
            刷新
          </button>
        </div>
      </div>

      {error ? <div className="panel border-rose-200 bg-rose-50 text-rose-700">{error}</div> : null}

      {data?.rows?.length ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {data.rows.map((row) => (
            <div key={row.symbol} className="panel space-y-3">
              <div className="flex items-center justify-between">
                <div className="text-base font-semibold text-slate-800">{row.symbol}</div>
                <span className="tag-muted">信号快照</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1">
                  最新价格: {row.latest_price ?? row.latest_close}
                  <span className="ml-1 text-xs text-slate-500">
                    ({row.latest_price_type || "K线收盘"})
                  </span>
                </div>
                <div className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1">RSI14: {row.rsi14}</div>
                <div className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1">MA5: {row.ma5}</div>
                <div className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1">MA20: {row.ma20}</div>
              </div>
              <div className="pt-1 text-sm text-slate-700">信号状态</div>

              <div className="grid grid-cols-1 gap-2">
                <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
                  RSI 超卖:
                  <span className={row.signals?.rsi_oversold ? "ml-2 text-rose-600" : "ml-2 text-emerald-600"}>
                    {row.signals?.rsi_oversold ? "是" : "否"}
                  </span>
                </div>

                <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
                  MA5 高于 MA20:
                  <span className={row.signals?.ma5_above_ma20 ? "ml-2 text-emerald-600" : "ml-2 text-slate-500"}>
                    {row.signals?.ma5_above_ma20 ? "是" : "否"}
                  </span>
                </div>

                <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
                  底部反转提示:
                  <span className={row.signals?.bottom_reversal_hint ? "ml-2 text-emerald-600" : "ml-2 text-slate-500"}>
                    {row.signals?.bottom_reversal_hint ? "触发" : "未触发"}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="panel">加载中...</div>
      )}
    </PageShell>
  );
}
