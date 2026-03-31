export const INPUT_CLS =
  "w-full rounded-lg border border-slate-700/80 bg-slate-950/70 px-3 py-2 text-sm text-slate-100 outline-none ring-0 placeholder:text-slate-500 focus:border-cyan-400/70 focus:shadow-[0_0_0_1px_rgba(34,211,238,0.35)]";
export const PANEL_TITLE_CLS = "text-sm font-semibold tracking-wide text-slate-100";
export const SUB_TITLE_CLS = "text-[11px] uppercase tracking-[0.14em] text-slate-400";

export const formatTime = (value?: string) => {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("zh-CN", { hour12: false });
};

export const mapQueueBusyError = (e: unknown, taskLabel: string): string | null => {
  const raw = String((e as { message?: string })?.message || e || "");
  if (!raw) return null;
  let parsed: any = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    /* ignore */
  }
  const detail = parsed?.detail ?? parsed;
  const errorCode = String(detail?.error || "").toLowerCase();
  if (!raw.includes("429") && errorCode !== "too_many_pending_tasks") {
    return null;
  }
  const maxPending = Number(detail?.max_pending || 0);
  const suffix = Number.isFinite(maxPending) && maxPending > 0 ? `（上限 ${maxPending}）` : "";
  return `${taskLabel}任务队列已满${suffix}，请稍后重试或先等待当前任务完成。`;
};
