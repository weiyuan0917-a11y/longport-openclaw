/**
 * 全站「最近使用的标的」：localStorage 持久化，供回测等页面默认填入。
 */
export const LAST_SYMBOL_STORAGE_KEY = "lp_ui_last_symbol_v1";

/** 从未保存过时的占位默认（避免再用固定 RXRX.US） */
export const LAST_SYMBOL_FALLBACK = "AAPL.US";

export function readLastSymbol(): string {
  if (typeof window === "undefined") return LAST_SYMBOL_FALLBACK;
  try {
    const s = localStorage.getItem(LAST_SYMBOL_STORAGE_KEY)?.trim();
    if (s) return s;
  } catch {
    /* ignore */
  }
  return LAST_SYMBOL_FALLBACK;
}

export function writeLastSymbol(symbol: string) {
  const t = String(symbol || "").trim();
  if (!t) return;
  try {
    localStorage.setItem(LAST_SYMBOL_STORAGE_KEY, t.toUpperCase());
  } catch {
    /* quota / 隐私模式 */
  }
}
