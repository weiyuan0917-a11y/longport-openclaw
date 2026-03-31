"use client";

import { useLayoutEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { LAST_SYMBOL_FALLBACK, readLastSymbol, writeLastSymbol } from "@/lib/last-symbol";
import { PageShell } from "@/components/ui/page-shell";
import { buildSwrOptions, SWR_INTERVALS } from "@/lib/swr-config";
import useSWR from "swr";

type FormState = {
  action: "buy" | "sell";
  symbol: string;
  quantity: number;
  price: string;
};

const mapOrderStatus = (status?: string) => {
  const normalize = String(status || "").split(".").pop() || "";
  const m: Record<string, string> = {
    New: "\u5f85\u6210\u4ea4",
    PartialFilled: "\u90e8\u5206\u6210\u4ea4",
    Filled: "\u5df2\u6210\u4ea4",
    Canceled: "\u5df2\u64a4\u5355",
    Cancelled: "\u5df2\u64a4\u5355",
    Rejected: "\u5df2\u62d2\u5355",
    Expired: "\u5df2\u8fc7\u671f",
    PendingCancel: "\u64a4\u5355\u4e2d",
    PendingReplace: "\u4fee\u6539\u4e2d",
  };
  if (!status) return "-";
  return m[normalize] || status;
};

const mapSide = (side?: string) => {
  if (!side) return "-";
  const v = side.toLowerCase();
  if (v.includes("buy")) return "\u4e70\u5165";
  if (v.includes("sell")) return "\u5356\u51fa";
  return side;
};

const orderStatusTone = (status?: string) => {
  const s = String(status || "").split(".").pop() || "";
  if (["Filled"].includes(s)) return "text-emerald-300";
  if (["Rejected", "Expired", "Canceled", "Cancelled"].includes(s)) return "text-rose-300";
  return "text-slate-300";
};

const fmtMoney = (v?: number) =>
  v == null ? "-" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const fmtNum = (v?: number, digits = 2) => (v == null ? "-" : Number(v).toFixed(digits));

/** 与持仓表格一致：优先接口浮动盈亏，否则用 pnl 或现价-成本估算 */
function positionRowPnl(p: Record<string, unknown>): number {
  const qty = Number(p.quantity ?? 0);
  const cost = Number(p.cost_price ?? 0);
  const last = Number(p.current_price ?? p.last_done ?? 0);
  if (p.unrealized_pl != null && Number.isFinite(Number(p.unrealized_pl))) {
    return Number(p.unrealized_pl);
  }
  if (p.pnl != null && Number.isFinite(Number(p.pnl))) {
    return Number(p.pnl);
  }
  return (last - cost) * qty;
}

export default function TradePage() {
  const [error, setError] = useState("");
  const [cancelling, setCancelling] = useState<Record<string, boolean>>({});
  const [form, setForm] = useState<FormState>({
    action: "buy",
    symbol: LAST_SYMBOL_FALLBACK,
    quantity: 100,
    price: "",
  });
  useLayoutEffect(() => {
    setForm((s) => ({ ...s, symbol: readLastSymbol() }));
  }, []);
  const { data: account, error: accountError, mutate: mutateAccount } = useSWR(
    "/trade/account",
    (path: string) => apiGet<any>(path),
    buildSwrOptions(SWR_INTERVALS.slowPage.refreshInterval, SWR_INTERVALS.slowPage.dedupingInterval)
  );
  const { data: positionsResp, error: positionsError, mutate: mutatePositions } = useSWR(
    "/trade/positions",
    (path: string) => apiGet<any>(path),
    buildSwrOptions(SWR_INTERVALS.slowPage.refreshInterval, SWR_INTERVALS.slowPage.dedupingInterval)
  );
  const { data: ordersResp, error: ordersError, mutate: mutateOrders } = useSWR(
    "/trade/orders",
    (path: string) => apiGet<any>(path),
    buildSwrOptions(SWR_INTERVALS.fastPoll.refreshInterval, SWR_INTERVALS.fastPoll.dedupingInterval)
  );
  const { data: risk, error: riskError, mutate: mutateRisk } = useSWR(
    "/risk/config",
    (path: string) => apiGet<any>(path),
    buildSwrOptions(SWR_INTERVALS.slowPage.refreshInterval, SWR_INTERVALS.slowPage.dedupingInterval)
  );
  const positions: any[] = positionsResp?.positions || [];
  const orders: any[] = ordersResp?.orders || [];
  const totalPositionPnl = useMemo(
    () => positions.reduce((sum, p) => sum + positionRowPnl(p), 0),
    [positions]
  );
  const dataErrorText = useMemo(() => {
    const errs: string[] = [];
    if (accountError) errs.push("资产信息");
    if (positionsError) errs.push("持仓数据");
    if (ordersError) errs.push("订单列表");
    if (riskError) errs.push("风控参数");
    return errs.length ? `部分数据加载失败：${errs.join("、")}，系统将继续自动重试` : "";
  }, [accountError, positionsError, ordersError, riskError]);

  const submitOrder = async () => {
    const msg = `\u8bf7\u786e\u8ba4${form.action === "buy" ? "\u4e70\u5165" : "\u5356\u51fa"} ${form.symbol} ${form.quantity}\u80a1`;
    if (!confirm(msg)) return;

    try {
      const payload: any = {
        action: form.action,
        symbol: form.symbol,
        quantity: Number(form.quantity),
      };
      if (form.price) payload.price = Number(form.price);
      await apiPost("/trade/order", payload);
      writeLastSymbol(form.symbol);
      await Promise.all([mutateAccount(), mutatePositions(), mutateOrders(), mutateRisk()]);
    } catch (e: any) {
      setError(String(e.message || e));
    }
  };

  const cancelOrder = async (orderId?: string) => {
    if (!orderId) return;
    if (!confirm(`\u786e\u8ba4\u64a4\u9500\u8ba2\u5355 ${orderId}\uff1f`)) return;
    setCancelling((s) => ({ ...s, [orderId]: true }));
    try {
      await apiPost(`/trade/order/${encodeURIComponent(orderId)}/cancel`, {});
      await mutateOrders();
      setError("");
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setCancelling((s) => ({ ...s, [orderId]: false }));
    }
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">{"\u4ea4\u6613\u9762\u677f"}</h1>
            <div className="mt-1 text-sm text-slate-300">下单执行 · 持仓追踪 · 订单状态监控</div>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="tag-muted">净资产 {fmtMoney(account?.net_assets)}</span>
            <span className="tag-muted">可用购买力 {fmtMoney(account?.buy_power)}</span>
            <span
              className={
                positions.length === 0
                  ? "tag-muted"
                  : totalPositionPnl >= 0
                    ? "rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-sm text-emerald-300"
                    : "rounded-md border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-sm text-rose-300"
              }
            >
              持仓盈亏 {positions.length ? fmtMoney(totalPositionPnl) : "—"}
            </span>
          </div>
        </div>
      </div>
      {error || dataErrorText ? (
        <div className="panel border-rose-200 bg-rose-50 text-rose-700">{error || dataErrorText}</div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="panel space-y-1">
          <div className="field-label">{"\u8d44\u4ea7\u6982\u89c8"}</div>
          <div>{"\u51c0\u8d44\u4ea7\uff1a"}{fmtMoney(account?.net_assets)}</div>
          <div>{"\u53ef\u7528\u8d2d\u4e70\u529b\uff1a"}{fmtMoney(account?.buy_power)}</div>
          <div>{"\u73b0\u91d1\uff1a"}{fmtMoney(account?.cash)}</div>
          <div className={totalPositionPnl >= 0 ? "text-emerald-300" : "text-rose-300"}>
            持仓浮动盈亏合计：{positions.length ? fmtMoney(totalPositionPnl) : "—"}
          </div>
        </div>

        <div className="panel space-y-1">
          <div className="field-label">{"\u98ce\u63a7\u53c2\u6570"}</div>
          <div>{"\u5355\u7b14\u6700\u5927\u91d1\u989d\uff1a"}{fmtMoney(risk?.max_order_amount)}</div>
          <div>{"\u5355\u65e5\u6700\u5927\u56de\u64a4\uff1a"}{risk?.max_daily_loss_pct != null ? `${(risk.max_daily_loss_pct * 100).toFixed(1)}%` : "-"}</div>
          <div>{"\u6b62\u635f\u6bd4\u4f8b\uff1a"}{risk?.stop_loss_pct != null ? `${(risk.stop_loss_pct * 100).toFixed(1)}%` : "-"}</div>
          <div>{"\u5355\u6807\u7684\u6700\u5927\u4ed3\u4f4d\uff1a"}{risk?.max_position_pct != null ? `${(risk.max_position_pct * 100).toFixed(1)}%` : "-"}</div>
          <div>{"\u98ce\u63a7\u5f00\u5173\uff1a"}{risk?.enabled ? "\u5df2\u542f\u7528" : "\u5df2\u5173\u95ed"}</div>
        </div>
      </div>

      <div className="panel">
        <div className="field-label">{"\u4e0b\u5355\uff08\u63d0\u4ea4\u524d\u4e8c\u6b21\u786e\u8ba4\uff09"}</div>
        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-5">
          <select
            className="input-base"
            value={form.action}
            onChange={(e) => setForm((s) => ({ ...s, action: e.target.value as "buy" | "sell" }))}
          >
            <option value="buy">{"\u4e70\u5165"}</option>
            <option value="sell">{"\u5356\u51fa"}</option>
          </select>

          <input
            className="input-base"
            value={form.symbol}
            onChange={(e) => setForm((s) => ({ ...s, symbol: e.target.value }))}
            onBlur={() => writeLastSymbol(form.symbol)}
            placeholder="code, e.g. 01810.HK"
          />

          <input
            className="input-base"
            type="number"
            value={form.quantity}
            onChange={(e) => setForm((s) => ({ ...s, quantity: Number(e.target.value) }))}
          />

          <input
            className="input-base"
            value={form.price}
            onChange={(e) => setForm((s) => ({ ...s, price: e.target.value }))}
            placeholder={"\u7559\u7a7a = \u5e02\u4ef7"}
          />

          <button className="btn-primary" onClick={submitOrder}>
            {"\u63d0\u4ea4\u8ba2\u5355"}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="section-title mb-2">{"\u6301\u4ed3"}</div>
        {!positions.length ? (
          <div className="text-sm text-slate-400">{"\u6682\u65e0\u6301\u4ed3"}</div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-2 md:hidden">
              {positions.map((p, idx) => {
                const qty = Number(p.quantity ?? 0);
                const cost = Number(p.cost_price ?? 0);
                const last = Number(p.current_price ?? p.last_done ?? 0);
                const pnl = positionRowPnl(p);
                const pnlPct = cost > 0 ? ((last - cost) / cost) * 100 : 0;
                return (
                  <div key={`${p.symbol || p.code || "posm"}-${idx}`} className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">
                    <div className="font-semibold text-slate-100">{p.symbol || p.code || "-"}</div>
                    <div className="mt-1 text-slate-300">数量: {qty}</div>
                    <div className="text-slate-300">成本/现价: {fmtNum(cost)} / {fmtNum(last)}</div>
                    <div className={pnl >= 0 ? "text-emerald-300" : "text-rose-300"}>浮动盈亏: {fmtNum(pnl)}</div>
                    <div className={pnlPct >= 0 ? "text-emerald-300" : "text-rose-300"}>盈亏百分比: {fmtNum(pnlPct)}%</div>
                  </div>
                );
              })}
            </div>
            <div className="table-shell hidden md:block">
            <table className="w-full min-w-[880px] text-sm">
              <thead className="table-head text-left">
                <tr>
                  <th className="px-3 py-2">{"\u6807\u7684"}</th>
                  <th className="px-3 py-2">{"\u6570\u91cf"}</th>
                  <th className="px-3 py-2">{"\u6210\u672c\u4ef7"}</th>
                  <th className="px-3 py-2">{"\u73b0\u4ef7"}</th>
                  <th className="px-3 py-2">{"\u6d6e\u52a8\u76c8\u4e8f"}</th>
                  <th className="px-3 py-2">{"\u76c8\u4e8f\u767e\u5206\u6bd4"}</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, idx) => {
                const qty = Number(p.quantity ?? 0);
                const cost = Number(p.cost_price ?? 0);
                const last = Number(p.current_price ?? p.last_done ?? 0);
                const pnl = positionRowPnl(p);
                const pnlPct = cost > 0 ? ((last - cost) / cost) * 100 : 0;

                return (
                  <tr key={`${p.symbol || p.code || "pos"}-${idx}`} className="border-t border-slate-800/90 hover:bg-slate-900/40">
                    <td className="px-3 py-2">{p.symbol || p.code || "-"}</td>
                    <td className="px-3 py-2">{qty}</td>
                    <td className="px-3 py-2">{fmtNum(cost)}</td>
                    <td className="px-3 py-2">{fmtNum(last)}</td>
                    <td className={`px-3 py-2 ${pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{fmtNum(pnl)}</td>
                    <td className={`px-3 py-2 ${pnlPct >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{fmtNum(pnlPct)}%</td>
                  </tr>
                );
                })}
              </tbody>
            </table>
            </div>
          </>
        )}
      </div>

      <div className="panel">
        <div className="section-title mb-2">{"\u8ba2\u5355\uff08\u6bcf5\u79d2\u81ea\u52a8\u5237\u65b0\uff09"}</div>
        {!orders.length ? (
          <div className="text-sm text-slate-400">{"\u6682\u65e0\u8ba2\u5355"}</div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-2 md:hidden">
              {orders.map((o, idx) => {
                const orderId = String(o.order_id || "");
                const status = String(o.status || "").split(".").pop() || "";
                const canCancel = ["New", "PartialFilled", "PendingCancel"].includes(status);
                return (
                  <div key={`${o.order_id || "ordm"}-${idx}`} className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">
                    <div className="flex items-center justify-between">
                      <div className="font-semibold text-slate-100">{o.symbol || "-"}</div>
                      <div className={orderStatusTone(o.status)}>{mapOrderStatus(o.status)}</div>
                    </div>
                    <div className="mt-1 text-slate-300">订单ID: {o.order_id || "-"}</div>
                    <div className="text-slate-300">方向/数量: {mapSide(o.side)} / {o.quantity ?? "-"}</div>
                    <div className="text-slate-300">价格: {o.price != null ? fmtNum(Number(o.price)) : "\u5e02\u4ef7"}</div>
                    {canCancel ? (
                      <button
                        className="mt-2 rounded-lg bg-gradient-to-r from-rose-600 to-red-600 px-2 py-1 text-xs text-white hover:opacity-90 disabled:opacity-50"
                        onClick={() => cancelOrder(orderId)}
                        disabled={!!cancelling[orderId]}
                      >
                        {cancelling[orderId] ? "\u64a4\u5355\u4e2d..." : "\u4e00\u952e\u64a4\u5355"}
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </div>
            <div className="table-shell hidden md:block">
            <table className="w-full min-w-[980px] text-sm">
              <thead className="table-head text-left">
                <tr>
                  <th className="px-3 py-2">{"\u8ba2\u5355 ID"}</th>
                  <th className="px-3 py-2">{"\u6807\u7684"}</th>
                  <th className="px-3 py-2">{"\u65b9\u5411"}</th>
                  <th className="px-3 py-2">{"\u6570\u91cf"}</th>
                  <th className="px-3 py-2">{"\u4ef7\u683c"}</th>
                  <th className="px-3 py-2">{"\u72b6\u6001"}</th>
                  <th className="px-3 py-2">{"\u64cd\u4f5c"}</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o, idx) => {
                const orderId = String(o.order_id || "");
                const status = String(o.status || "").split(".").pop() || "";
                const canCancel = ["New", "PartialFilled", "PendingCancel"].includes(status);
                return (
                  <tr key={`${o.order_id || "ord"}-${idx}`} className="border-t border-slate-800/90 hover:bg-slate-900/40">
                    <td className="px-3 py-2">{o.order_id || "-"}</td>
                    <td className="px-3 py-2">{o.symbol || "-"}</td>
                    <td className="px-3 py-2">{mapSide(o.side)}</td>
                    <td className="px-3 py-2">{o.quantity ?? "-"}</td>
                    <td className="px-3 py-2">{o.price != null ? fmtNum(Number(o.price)) : "\u5e02\u4ef7"}</td>
                    <td className={`px-3 py-2 ${orderStatusTone(o.status)}`}>{mapOrderStatus(o.status)}</td>
                    <td className="px-3 py-2">
                      {canCancel ? (
                        <button
                          className="rounded-lg bg-gradient-to-r from-rose-600 to-red-600 px-2 py-1 text-xs text-white hover:opacity-90 disabled:opacity-50"
                          onClick={() => cancelOrder(orderId)}
                          disabled={!!cancelling[orderId]}
                        >
                          {cancelling[orderId] ? "\u64a4\u5355\u4e2d..." : "\u4e00\u952e\u64a4\u5355"}
                        </button>
                      ) : (
                        <span className="text-xs text-slate-500">-</span>
                      )}
                    </td>
                  </tr>
                );
                })}
              </tbody>
            </table>
            </div>
          </>
        )}
      </div>
    </PageShell>
  );
}
