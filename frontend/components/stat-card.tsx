'use client';

export function StatCard({ title, value, sub }: { title: string; value: string | number; sub?: string }) {
  return (
    <div className="metric-card">
      <div className="field-label">{title}</div>
      <div className="mt-1 text-3xl font-semibold tracking-tight text-slate-900">{value}</div>
      {sub ? <div className="mt-1 text-xs text-slate-500">{sub}</div> : null}
    </div>
  );
}
