"use client";

import { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { PageShell } from "@/components/ui/page-shell";
import { buildSwrOptions, SWR_INTERVALS } from "@/lib/swr-config";
import useSWR from "swr";

type Leg = { symbol: string; side: "buy" | "sell"; contracts: number; price: string };
type OptionTemplate = "bull_call_spread" | "bear_put_spread" | "straddle" | "strangle";

type OptionLegQuote = {
  last_done?: number | null;
  prev_close?: number | null;
  volume?: number | null;
  timestamp?: string | null;
};

function formatOptionLast(q: OptionLegQuote | null | undefined): string {
  if (!q || q.last_done == null || Number.isNaN(Number(q.last_done))) return "—";
  return Number(q.last_done).toFixed(2);
}

function optionQuoteTitle(side: string, q: OptionLegQuote | null | undefined): string | undefined {
  if (!q?.timestamp && q?.prev_close == null && (q?.volume == null || q.volume === 0)) return undefined;
  const parts = [`${side} 行情`];
  if (q.last_done != null) parts.push(`最新 ${Number(q.last_done).toFixed(4)}`);
  if (q.prev_close != null) parts.push(`昨收 ${Number(q.prev_close).toFixed(4)}`);
  if (q.volume != null && q.volume > 0) parts.push(`量 ${q.volume}`);
  if (q.timestamp) parts.push(`时间 ${q.timestamp}`);
  return parts.join(" · ");
}

const BT_FORM_STORAGE_KEY = "options_backtest_form_v1";

export default function OptionsPage() {
  const [symbol, setSymbol] = useState("AAPL.US");
  const [expiry, setExpiry] = useState("");
  const [expiries, setExpiries] = useState<string[]>([]);
  const [chain, setChain] = useState<any[]>([]);
  const [token, setToken] = useState("");
  const [legs, setLegs] = useState<Leg[]>([
    { symbol: "", side: "buy", contracts: 1, price: "" },
    { symbol: "", side: "sell", contracts: 1, price: "" },
  ]);
  const [feeEstimate, setFeeEstimate] = useState<any>(null);
  const [btTemplate, setBtTemplate] = useState<OptionTemplate>("straddle");
  const [btDays, setBtDays] = useState(180);
  const [btHoldingDays, setBtHoldingDays] = useState(20);
  const [btContracts, setBtContracts] = useState(1);
  const [btWidthPct, setBtWidthPct] = useState("0.05");
  const [btResult, setBtResult] = useState<any>(null);
  const [btLoading, setBtLoading] = useState(false);
  const [message, setMessage] = useState("");
  const { data: ordersResp, mutate: mutateOrders } = useSWR(
    "/options/orders",
    (path: string) => apiGet<any>(path),
    buildSwrOptions(SWR_INTERVALS.normalPoll.refreshInterval, SWR_INTERVALS.normalPoll.dedupingInterval)
  );
  const { data: positionsResp, mutate: mutatePositions } = useSWR(
    "/options/positions",
    (path: string) => apiGet<any>(path),
    buildSwrOptions(SWR_INTERVALS.normalPoll.refreshInterval, SWR_INTERVALS.normalPoll.dedupingInterval)
  );
  const orders: any[] = ordersResp?.orders || [];
  const positions: any[] = positionsResp?.positions || [];

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(BT_FORM_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      const templates: OptionTemplate[] = ["bull_call_spread", "bear_put_spread", "straddle", "strangle"];
      if (templates.includes(parsed?.btTemplate)) setBtTemplate(parsed.btTemplate);
      if (Number.isFinite(Number(parsed?.btDays))) setBtDays(Number(parsed.btDays));
      if (Number.isFinite(Number(parsed?.btHoldingDays))) setBtHoldingDays(Number(parsed.btHoldingDays));
      if (Number.isFinite(Number(parsed?.btContracts))) setBtContracts(Number(parsed.btContracts));
      if (typeof parsed?.btWidthPct === "string") setBtWidthPct(parsed.btWidthPct);
    } catch {
      // Ignore invalid local cache.
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        BT_FORM_STORAGE_KEY,
        JSON.stringify({
          btTemplate,
          btDays,
          btHoldingDays,
          btContracts,
          btWidthPct,
        })
      );
    } catch {
      // Ignore storage failures (private mode / quota).
    }
  }, [btTemplate, btDays, btHoldingDays, btContracts, btWidthPct]);

  const canSubmit = useMemo(
    () => legs.filter((x) => x.symbol.trim()).length > 0 && token.trim().length > 0,
    [legs, token]
  );

  const loadExpiries = async () => {
    try {
      const r = await apiGet<any>(`/options/expiries?symbol=${encodeURIComponent(symbol)}`);
      setExpiries(r.expiries || []);
      if (!expiry && r.expiries?.length) setExpiry(String(r.expiries[0]));
      setMessage("");
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const loadChain = async () => {
    try {
      const params = new URLSearchParams({ symbol, limit: "80" });
      if (expiry) params.set("expiry_date", expiry);
      const r = await apiGet<any>(`/options/chain?${params.toString()}`);
      setChain(r.options || []);
      setMessage("");
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const estimateFee = async () => {
    try {
      const payload = {
        legs: legs
          .filter((x) => x.symbol.trim())
          .map((x) => ({
            symbol: x.symbol.trim(),
            side: x.side,
            contracts: Number(x.contracts),
            price: Number(x.price || 0),
          })),
      };
      const r = await apiPost<any>("/options/fee-estimate", payload);
      setFeeEstimate(r.estimate);
      setMessage("");
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const submitOrder = async () => {
    if (!confirm("确认提交期权订单？")) return;
    try {
      const payload = {
        legs: legs
          .filter((x) => x.symbol.trim())
          .map((x) => ({
            symbol: x.symbol.trim(),
            side: x.side,
            contracts: Number(x.contracts),
            price: Number(x.price || 0),
          })),
        confirmation_token: token.trim(),
      };
      const r = await apiPost<any>("/options/order", payload);
      setMessage(`下单成功: ${JSON.stringify(r)}`);
      await loadOrders();
      await loadPositions();
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const loadOrders = async () => {
    try {
      await mutateOrders();
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const loadPositions = async () => {
    try {
      await mutatePositions();
    } catch (e: any) {
      setMessage(String(e.message || e));
    }
  };

  const runOptionBacktest = async () => {
    try {
      setBtLoading(true);
      const payload = {
        symbol: symbol.trim(),
        template: btTemplate,
        days: Number(btDays),
        holding_days: Number(btHoldingDays),
        contracts: Number(btContracts),
        width_pct: Number(btWidthPct || "0.05"),
      };
      const r = await apiPost<any>("/options/backtest", payload, { timeoutMs: 60000, retries: 0 });
      setBtResult(r);
      setMessage("");
    } catch (e: any) {
      setMessage(String(e.message || e));
    } finally {
      setBtLoading(false);
    }
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">期权交易中心</h1>
            <div className="mt-1 text-sm text-slate-300">期权链路 · 多腿建仓 · 成本试算 · 策略回测</div>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="tag-muted">标的 {symbol || "-"}</span>
            <span className="tag-muted">到期日 {expiry || "未选择"}</span>
          </div>
        </div>
      </div>
      {message ? <div className="panel border-amber-200 bg-amber-50 text-amber-700">{message}</div> : null}

      <div className="panel space-y-3">
        <div className="section-title">期权链路查询</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
          <input className="input-base" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          <button className="btn-secondary" onClick={loadExpiries}>
            加载到期日
          </button>
          <select className="input-base" value={expiry} onChange={(e) => setExpiry(e.target.value)}>
            <option value="">选择到期日</option>
            {expiries.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </select>
          <button className="btn-primary" onClick={loadChain}>
            查询期权链
          </button>
        </div>
        <p className="text-xs text-slate-500">
          最新价来自 LongPort 实时行情（<span className="font-mono">last_done</span>）；需账户具备美股期权（OPRA）行情权限，无权限或无成交时可能显示「—」。
        </p>
        <div className="table-shell overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="table-head text-left">
              <tr>
                <th className="px-3 py-2">执行价</th>
                <th className="px-3 py-2">Call 代码</th>
                <th className="px-3 py-2 text-right">Call 最新</th>
                <th className="px-3 py-2">Put 代码</th>
                <th className="px-3 py-2 text-right">Put 最新</th>
              </tr>
            </thead>
            <tbody>
              {chain.map((x, idx) => (
                <tr key={`${x.strike_price}-${idx}`} className="border-t border-slate-800/90">
                  <td className="px-3 py-2 font-mono">{x.strike_price ?? "-"}</td>
                  <td className="max-w-[200px] truncate px-3 py-2 font-mono text-xs" title={x.call_symbol || undefined}>
                    {x.call_symbol || "—"}
                  </td>
                  <td
                    className="px-3 py-2 text-right font-mono text-cyan-200"
                    title={optionQuoteTitle("Call", x.call_quote as OptionLegQuote | undefined)}
                  >
                    {formatOptionLast(x.call_quote as OptionLegQuote | undefined)}
                  </td>
                  <td className="max-w-[200px] truncate px-3 py-2 font-mono text-xs" title={x.put_symbol || undefined}>
                    {x.put_symbol || "—"}
                  </td>
                  <td
                    className="px-3 py-2 text-right font-mono text-cyan-200"
                    title={optionQuoteTitle("Put", x.put_quote as OptionLegQuote | undefined)}
                  >
                    {formatOptionLast(x.put_quote as OptionLegQuote | undefined)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel space-y-3">
        <div className="section-title">策略建仓器 + 下单确认</div>
        {legs.map((leg, idx) => (
          <div key={idx} className="grid grid-cols-1 gap-2 md:grid-cols-4">
            <input
              className="input-base"
              placeholder="期权代码"
              value={leg.symbol}
              onChange={(e) => setLegs((s) => s.map((x, i) => (i === idx ? { ...x, symbol: e.target.value } : x)))}
            />
            <select
              className="input-base"
              value={leg.side}
              onChange={(e) => setLegs((s) => s.map((x, i) => (i === idx ? { ...x, side: e.target.value as "buy" | "sell" } : x)))}
            >
              <option value="buy">买入</option>
              <option value="sell">卖出</option>
            </select>
            <input
              className="input-base"
              type="number"
              value={leg.contracts}
              onChange={(e) => setLegs((s) => s.map((x, i) => (i === idx ? { ...x, contracts: Number(e.target.value) } : x)))}
            />
            <input
              className="input-base"
              placeholder="限价(可空)"
              value={leg.price}
              onChange={(e) => setLegs((s) => s.map((x, i) => (i === idx ? { ...x, price: e.target.value } : x)))}
            />
          </div>
        ))}
        <input
          className="input-base"
          placeholder="confirmation_token (L3 必填)"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={estimateFee}>
            试算费用
          </button>
          <button className="btn-primary" disabled={!canSubmit} onClick={submitOrder}>
            确认下单
          </button>
        </div>
        {feeEstimate ? (
          <div className="rounded-lg border border-slate-700/70 p-3 text-sm text-slate-300">
            总费用: {feeEstimate.total_fee} | 最大亏损估算: {feeEstimate.max_loss_estimate}
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="panel">
          <div className="mb-2 flex items-center justify-between">
            <div className="field-label">期权订单</div>
            <button className="btn-secondary" onClick={loadOrders}>
              刷新
            </button>
          </div>
          <div className="space-y-2 text-sm">
            {orders.map((o, idx) => (
              <div key={`${o.order_id}-${idx}`} className="rounded border border-slate-700/60 p-2">
                {o.symbol} | {o.side} | {o.quantity} | {o.status}
              </div>
            ))}
          </div>
        </div>
        <div className="panel">
          <div className="mb-2 flex items-center justify-between">
            <div className="field-label">期权持仓</div>
            <button className="btn-secondary" onClick={loadPositions}>
              刷新
            </button>
          </div>
          <div className="space-y-2 text-sm">
            {positions.map((p, idx) => (
              <div key={`${p.symbol}-${idx}`} className="rounded border border-slate-700/60 p-2">
                {p.symbol} | 数量 {p.quantity} | PnL {p.pnl}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="panel space-y-3">
        <div className="section-title">期权回测</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-6">
          <label className="space-y-1">
            <div className="field-label" title="选择期权策略模板：牛市价差/熊市价差/跨式/宽跨式。">
              策略模板
            </div>
            <select
              className="input-base"
              value={btTemplate}
              onChange={(e) => setBtTemplate(e.target.value as OptionTemplate)}
            >
              <option value="bull_call_spread">Bull Call Spread</option>
              <option value="bear_put_spread">Bear Put Spread</option>
              <option value="straddle">Straddle</option>
              <option value="strangle">Strangle</option>
            </select>
          </label>
          <label className="space-y-1">
            <div className="field-label" title="使用最近多少天历史数据进行回测，数值越大覆盖周期越长。">
              回测天数
            </div>
            <input
              className="input-base"
              type="number"
              min={30}
              max={1500}
              value={btDays}
              onChange={(e) => setBtDays(Number(e.target.value))}
              placeholder="例如 180"
            />
          </label>
          <label className="space-y-1">
            <div className="field-label" title="每笔模拟交易持有多久后平仓，影响交易频率和风险暴露。">
              持有天数
            </div>
            <input
              className="input-base"
              type="number"
              min={3}
              max={120}
              value={btHoldingDays}
              onChange={(e) => setBtHoldingDays(Number(e.target.value))}
              placeholder="例如 20"
            />
          </label>
          <label className="space-y-1">
            <div className="field-label" title="每笔交易使用的期权合约张数，通常 1 张=100 股名义规模。">
              合约手数
            </div>
            <input
              className="input-base"
              type="number"
              min={1}
              max={50}
              value={btContracts}
              onChange={(e) => setBtContracts(Number(e.target.value))}
              placeholder="例如 1"
            />
          </label>
          <label className="space-y-1">
            <div className="field-label" title="执行价间距比例（如 0.05 表示 5%），用于价差/宽跨式模板。">
              价差宽度%
            </div>
            <input className="input-base" value={btWidthPct} onChange={(e) => setBtWidthPct(e.target.value)} placeholder="例如 0.05" />
          </label>
          <div className="space-y-1">
            <div className="field-label opacity-0">运行</div>
            <button className="btn-primary w-full" onClick={runOptionBacktest} disabled={btLoading || !symbol.trim()}>
              {btLoading ? "回测中..." : "运行回测"}
            </button>
          </div>
        </div>

        {btResult?.stats ? (
          <div className="space-y-3">
            <div className="grid grid-cols-1 gap-2 md:grid-cols-4 text-sm">
              <div className="rounded border border-slate-700/70 p-2">总交易: {btResult.stats.total_trades}</div>
              <div className="rounded border border-slate-700/70 p-2">胜率: {btResult.stats.win_rate_pct}%</div>
              <div className="rounded border border-slate-700/70 p-2">净收益: {btResult.stats.total_net_pnl}</div>
              <div className="rounded border border-slate-700/70 p-2">收益率: {btResult.stats.total_return_pct}%</div>
            </div>
            <div className="rounded border border-slate-700/70 p-2 text-sm">
              总费用: {btResult.stats.total_fee} | 费用拆分:{" "}
              {Object.entries(btResult.stats.fee_breakdown || {})
                .map(([k, v]) => `${k}:${v}`)
                .join(" | ") || "-"}
            </div>
            <div className="table-shell">
              <table className="w-full text-sm">
                <thead className="table-head text-left">
                  <tr>
                    <th className="px-3 py-2">开仓</th>
                    <th className="px-3 py-2">平仓</th>
                    <th className="px-3 py-2">入场价</th>
                    <th className="px-3 py-2">出场价</th>
                    <th className="px-3 py-2">毛收益</th>
                    <th className="px-3 py-2">费用</th>
                    <th className="px-3 py-2">净收益</th>
                  </tr>
                </thead>
                <tbody>
                  {(btResult.trades || []).slice(0, 30).map((t: any, idx: number) => (
                    <tr key={`${t.entry_date}-${idx}`} className="border-t border-slate-800/90">
                      <td className="px-3 py-2">{t.entry_date}</td>
                      <td className="px-3 py-2">{t.exit_date}</td>
                      <td className="px-3 py-2">{t.entry_spot}</td>
                      <td className="px-3 py-2">{t.exit_spot}</td>
                      <td className="px-3 py-2">{t.gross_pnl}</td>
                      <td className="px-3 py-2">{t.fee}</td>
                      <td className={`px-3 py-2 ${Number(t.net_pnl) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                        {t.net_pnl}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </div>
    </PageShell>
  );
}
