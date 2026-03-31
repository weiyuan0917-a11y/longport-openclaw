import { apiGet, apiPost } from "@/lib/api";

type RequestOptions = {
  timeoutMs?: number;
  retries?: number;
};

function isMethodNotAllowed(err: unknown): boolean {
  const s = String((err instanceof Error ? err.message : err) || "");
  if (/method not allowed/i.test(s)) return true;
  if (/\b405\b/.test(s)) return true;
  try {
    const j = JSON.parse(s) as { detail?: unknown };
    const d = j?.detail;
    if (typeof d === "string" && /method not allowed/i.test(d)) return true;
  } catch {
    /* 非 JSON */
  }
  return false;
}

/**
 * 将 POST body 中 GET /backtest/compare 支持的字段转为查询串（不含 bars / strategy_params）。
 */
export function backtestComparePayloadToQuery(payload: Record<string, unknown>): string {
  const p = new URLSearchParams();
  const set = (k: string, v: unknown) => {
    if (v === undefined || v === null) return;
    if (typeof v === "boolean") {
      p.set(k, v ? "true" : "false");
      return;
    }
    if (typeof v === "number" && Number.isFinite(v)) {
      p.set(k, String(v));
      return;
    }
    if (typeof v === "string" && v !== "") {
      p.set(k, v);
    }
  };

  set("symbol", payload.symbol);
  set("days", payload.days);
  set("periods", payload.periods);
  set("kline", payload.kline);
  set("initial_capital", payload.initial_capital);
  set("execution_mode", payload.execution_mode);
  set("slippage_bps", payload.slippage_bps);
  set("commission_bps", payload.commission_bps);
  set("stamp_duty_bps", payload.stamp_duty_bps);
  set("walk_forward_windows", payload.walk_forward_windows);
  set("ml_filter_enabled", payload.ml_filter_enabled);
  set("ml_model_type", payload.ml_model_type);
  set("ml_threshold", payload.ml_threshold);
  set("ml_horizon_days", payload.ml_horizon_days);
  set("ml_train_ratio", payload.ml_train_ratio);
  set("include_trades", payload.include_trades);
  set("trade_limit", payload.trade_limit);
  set("trade_offset", payload.trade_offset);
  set("strategy_key", payload.strategy_key);
  set("include_best_kline", payload.include_best_kline);
  set("use_server_kline_cache", payload.use_server_kline_cache);

  return p.toString();
}

export type BacktestCompareResult<T> = { data: T; usedGetFallback: boolean };

/**
 * 优先 POST（支持 K 线缓存、策略参数）；若后端为旧版仅注册 GET，则自动降级为 GET，避免 405。
 */
export async function apiBacktestCompare<T = unknown>(
  payload: Record<string, unknown>,
  options?: RequestOptions,
): Promise<BacktestCompareResult<T>> {
  try {
    const data = await apiPost<T>("/backtest/compare", payload, options);
    return { data, usedGetFallback: false };
  } catch (e: unknown) {
    if (!isMethodNotAllowed(e)) throw e;
    const q = backtestComparePayloadToQuery(payload);
    const data = await apiGet<T>(`/backtest/compare?${q}`, options);
    return { data, usedGetFallback: true };
  }
}
