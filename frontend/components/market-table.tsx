export type MarketRow = {
  symbol: string;
  name: string;
  last: number;
  change_pct: number;
};

export function MarketTable({ title, rows }: { title: string; rows: MarketRow[] }) {
  return (
    <div className="table-card">
      <div className="mb-3 text-sm font-semibold text-slate-800">{title}</div>
      <div className="table-shell">
        <table className="min-w-full text-sm">
          <thead className="table-head">
            <tr className="text-left">
              <th className="px-3 py-2">名称</th>
              <th className="px-3 py-2">最新价</th>
              <th className="px-3 py-2">涨跌幅</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} className="border-t border-slate-200 hover:bg-blue-50">
                <td className="px-3 py-2 text-slate-800">{r.name}</td>
                <td className="px-3 py-2">{r.last?.toFixed?.(2) ?? r.last}</td>
                <td className={"px-3 py-2 " + (r.change_pct >= 0 ? "text-emerald-600" : "text-rose-600")}>
                  {r.change_pct >= 0 ? "+" : ""}
                  {r.change_pct?.toFixed?.(2) ?? r.change_pct}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
