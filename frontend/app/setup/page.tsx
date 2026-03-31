"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { PageShell } from "@/components/ui/page-shell";

type SetupStatus = {
  configured: {
    longport: boolean;
    feishu: boolean;
    market_apis: boolean;
    openbb?: boolean;
  };
  values: Record<string, string>;
};

type RiskCfg = {
  max_order_amount: number;
  max_daily_loss_pct: number;
  stop_loss_pct: number;
  max_position_pct: number;
  enabled: boolean;
};

type LongPortDiag = {
  connection_limit: number;
  active_connections_api_process: number;
  usage_pct_api_process: number;
  estimated_connections_total?: number;
  estimated_usage_pct_total?: number;
  estimated_breakdown?: {
    api_active: number;
    mcp_estimated: number;
    feishu_estimated: number;
  };
  processes?: {
    api?: { pid?: number; running?: boolean };
    mcp?: { pid?: number | null; running?: boolean };
    feishu_bot?: { pid?: number | null; running?: boolean };
  };
  quote_ctx_ready: boolean;
  trade_ctx_ready: boolean;
  last_init_at?: string | null;
  last_error?: string | null;
  probe?: { requested?: boolean; ok?: boolean | null; error?: string | null };
  alert_level?: "ok" | "notice" | "warning" | "critical";
  recommendations?: string[];
  note?: string;
};

type FeeScheduleResponse = {
  version: string;
  schedule: Record<string, any>;
};

type FeeFormState = {
  hk_commission_enabled: boolean;
  hk_commission_rate_pct: number;
  hk_commission_min: number;
  hk_platform_fee: number;
  hk_stamp_duty_pct: number;
  hk_trading_fee_pct: number;
  hk_sfc_levy_pct: number;
  hk_afrc_levy_pct: number;
  hk_ccass_fee_pct: number;
  us_platform_per_share: number;
  us_platform_min: number;
  us_platform_max_pct_notional: number;
  us_settlement_per_share: number;
  us_settlement_max_pct_notional: number;
  us_taf_per_share: number;
  us_taf_min: number;
  us_taf_max: number;
  us_option_commission_per_contract: number;
  us_option_commission_min: number;
  us_option_platform_per_contract: number;
  us_option_platform_min: number;
  us_option_settlement_per_contract: number;
  us_option_regulatory_per_contract: number;
  us_option_clearing_per_contract: number;
  us_option_taf_per_contract: number;
  us_option_taf_min: number;
};

const feeNum = (v: any, fallback = 0): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
};

const fmtNum = (v: any, digits = 4): string => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : "-";
};

const emptyFeeForm: FeeFormState = {
  hk_commission_enabled: false,
  hk_commission_rate_pct: 0,
  hk_commission_min: 0,
  hk_platform_fee: 0,
  hk_stamp_duty_pct: 0,
  hk_trading_fee_pct: 0,
  hk_sfc_levy_pct: 0,
  hk_afrc_levy_pct: 0,
  hk_ccass_fee_pct: 0,
  us_platform_per_share: 0,
  us_platform_min: 0,
  us_platform_max_pct_notional: 0,
  us_settlement_per_share: 0,
  us_settlement_max_pct_notional: 0,
  us_taf_per_share: 0,
  us_taf_min: 0,
  us_taf_max: 0,
  us_option_commission_per_contract: 0,
  us_option_commission_min: 0,
  us_option_platform_per_contract: 0,
  us_option_platform_min: 0,
  us_option_settlement_per_contract: 0,
  us_option_regulatory_per_contract: 0,
  us_option_clearing_per_contract: 0,
  us_option_taf_per_contract: 0,
  us_option_taf_min: 0,
};

const scheduleToFeeForm = (s: Record<string, any>): FeeFormState => {
  const hk = s?.hk_stock || {};
  const us = s?.us_stock || {};
  const opt = s?.us_option_regular || {};
  return {
    hk_commission_enabled: Boolean(hk?.commission?.enabled),
    hk_commission_rate_pct: feeNum(hk?.commission?.rate_pct),
    hk_commission_min: feeNum(hk?.commission?.min_per_order),
    hk_platform_fee: feeNum(hk?.platform_fee?.amount),
    hk_stamp_duty_pct: feeNum(hk?.stamp_duty?.rate_pct),
    hk_trading_fee_pct: feeNum(hk?.trading_fee?.rate_pct),
    hk_sfc_levy_pct: feeNum(hk?.sfc_levy?.rate_pct),
    hk_afrc_levy_pct: feeNum(hk?.afrc_levy?.rate_pct),
    hk_ccass_fee_pct: feeNum(hk?.ccass_fee?.rate_pct),
    us_platform_per_share: feeNum(us?.platform_fee?.amount_per_share),
    us_platform_min: feeNum(us?.platform_fee?.min_per_order),
    us_platform_max_pct_notional: feeNum(us?.platform_fee?.max_pct_of_notional),
    us_settlement_per_share: feeNum(us?.settlement_fee?.amount_per_share),
    us_settlement_max_pct_notional: feeNum(us?.settlement_fee?.max_pct_of_notional),
    us_taf_per_share: feeNum(us?.taf?.amount_per_share),
    us_taf_min: feeNum(us?.taf?.min_per_order),
    us_taf_max: feeNum(us?.taf?.max_per_order),
    us_option_commission_per_contract: feeNum(opt?.commission?.amount_per_contract),
    us_option_commission_min: feeNum(opt?.commission?.min_per_order),
    us_option_platform_per_contract: feeNum(opt?.platform_fee?.amount_per_contract),
    us_option_platform_min: feeNum(opt?.platform_fee?.min_per_order),
    us_option_settlement_per_contract: feeNum(opt?.option_settlement_fee?.amount_per_contract),
    us_option_regulatory_per_contract: feeNum(opt?.option_regulatory_fee?.amount_per_contract),
    us_option_clearing_per_contract: feeNum(opt?.option_clearing_fee?.amount_per_contract),
    us_option_taf_per_contract: feeNum(opt?.option_taf?.amount_per_contract),
    us_option_taf_min: feeNum(opt?.option_taf?.min_per_order),
  };
};

const feeFormToSchedulePatch = (f: FeeFormState): Record<string, any> => ({
  hk_stock: {
    commission: { enabled: Boolean(f.hk_commission_enabled), rate_pct: feeNum(f.hk_commission_rate_pct), min_per_order: feeNum(f.hk_commission_min) },
    platform_fee: { amount: feeNum(f.hk_platform_fee) },
    stamp_duty: { rate_pct: feeNum(f.hk_stamp_duty_pct) },
    trading_fee: { rate_pct: feeNum(f.hk_trading_fee_pct) },
    sfc_levy: { rate_pct: feeNum(f.hk_sfc_levy_pct) },
    afrc_levy: { rate_pct: feeNum(f.hk_afrc_levy_pct) },
    ccass_fee: { rate_pct: feeNum(f.hk_ccass_fee_pct) },
  },
  us_stock: {
    platform_fee: {
      amount_per_share: feeNum(f.us_platform_per_share),
      min_per_order: feeNum(f.us_platform_min),
      max_pct_of_notional: feeNum(f.us_platform_max_pct_notional),
    },
    settlement_fee: {
      amount_per_share: feeNum(f.us_settlement_per_share),
      max_pct_of_notional: feeNum(f.us_settlement_max_pct_notional),
    },
    taf: {
      amount_per_share: feeNum(f.us_taf_per_share),
      min_per_order: feeNum(f.us_taf_min),
      max_per_order: feeNum(f.us_taf_max),
    },
  },
  us_option_regular: {
    commission: {
      amount_per_contract: feeNum(f.us_option_commission_per_contract),
      min_per_order: feeNum(f.us_option_commission_min),
    },
    platform_fee: {
      amount_per_contract: feeNum(f.us_option_platform_per_contract),
      min_per_order: feeNum(f.us_option_platform_min),
    },
    option_settlement_fee: { amount_per_contract: feeNum(f.us_option_settlement_per_contract) },
    option_regulatory_fee: { amount_per_contract: feeNum(f.us_option_regulatory_per_contract) },
    option_clearing_fee: { amount_per_contract: feeNum(f.us_option_clearing_per_contract) },
    option_taf: { amount_per_contract: feeNum(f.us_option_taf_per_contract), min_per_order: feeNum(f.us_option_taf_min) },
  },
});

export default function SetupPage() {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [risk, setRisk] = useState<RiskCfg | null>(null);
  const [services, setServices] = useState<any>(null);
  const [diag, setDiag] = useState<LongPortDiag | null>(null);
  const [saving, setSaving] = useState(false);
  const [testingOpenbb, setTestingOpenbb] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stoppingAll, setStoppingAll] = useState(false);
  const [savingFees, setSavingFees] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [feeScheduleText, setFeeScheduleText] = useState("");
  const [feeAdvancedMode, setFeeAdvancedMode] = useState(false);
  const [feeForm, setFeeForm] = useState<FeeFormState>(emptyFeeForm);
  const [feeEstimate, setFeeEstimate] = useState<any>(null);
  const [feeEstimateForm, setFeeEstimateForm] = useState({
    asset_class: "stock" as "stock" | "us_option",
    market: "US" as "HK" | "US" | "CN" | "OTHER",
    side: "buy" as "buy" | "sell",
    quantity: 100,
    price: 10,
  });

  const [form, setForm] = useState({
    longport_app_key: "",
    longport_app_secret: "",
    longport_access_token: "",
    feishu_app_id: "",
    feishu_app_secret: "",
    feishu_scheduled_chat_id: "",
    finnhub_api_key: "",
    tiingo_api_key: "",
    fred_api_key: "",
    coingecko_api_key: "",
    openclaw_mcp_max_level: "",
    openclaw_mcp_allow_l3: "",
    openclaw_mcp_l3_confirmation_token: "",
    openbb_enabled: "",
    openbb_base_url: "",
    openbb_timeout_seconds: "",
  });

  const load = async () => {
    try {
      setStatusLoading(true);
      // 配置状态先返回，避免首屏长时间显示“未配置”误导用户。
      const s = await apiGet<SetupStatus>("/setup/config");
      setStatus(s);
      setStatusLoading(false);

      const [rRes, svcRes, dgRes, feesRes] = await Promise.allSettled([
        apiGet<RiskCfg>("/risk/config"),
        apiGet<any>("/setup/services/status"),
        apiGet<LongPortDiag>("/setup/longport/diagnostics"),
        apiGet<FeeScheduleResponse>("/fees/schedule"),
      ]);

      if (rRes.status === "fulfilled") setRisk(rRes.value);
      if (svcRes.status === "fulfilled") setServices(svcRes.value);
      if (dgRes.status === "fulfilled") setDiag(dgRes.value);
      if (feesRes.status === "fulfilled") {
        setFeeScheduleText(JSON.stringify(feesRes.value?.schedule || {}, null, 2));
        setFeeForm(scheduleToFeeForm(feesRes.value?.schedule || {}));
      }
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
      setStatusLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const saveSecrets = async () => {
    setSaving(true);
    try {
      const payload: Record<string, string> = {};
      Object.entries(form).forEach(([k, v]) => {
        if (v.trim()) payload[k] = v.trim();
      });
      if (!Object.keys(payload).length) {
        setMsg("未填写新值，未执行保存。");
        setSaving(false);
        return;
      }
      const resp = await apiPost<{ restart_recommended?: boolean }>("/setup/config", payload);
      setMsg(resp?.restart_recommended ? "配置已保存到 .env，建议重启后端使所有进程一致生效。" : "配置已保存到 .env。");
      setForm({
        longport_app_key: "",
        longport_app_secret: "",
        longport_access_token: "",
        feishu_app_id: "",
        feishu_app_secret: "",
        feishu_scheduled_chat_id: "",
        finnhub_api_key: "",
        tiingo_api_key: "",
        fred_api_key: "",
        coingecko_api_key: "",
        openclaw_mcp_max_level: "",
        openclaw_mcp_allow_l3: "",
        openclaw_mcp_l3_confirmation_token: "",
        openbb_enabled: "",
        openbb_base_url: "",
        openbb_timeout_seconds: "",
      });
      await load();
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  const testOpenbb = async () => {
    setTestingOpenbb(true);
    try {
      const r = await apiGet<any>("/research/external/openbb/health");
      const enabled = Boolean(r?.health?.enabled);
      const ok = Boolean(r?.health?.ok);
      const base = r?.health?.base_url || status?.values?.openbb_base_url || "未设置";
      if (enabled && ok) {
        setMsg(`OpenBB 连接正常（${base}）`);
      } else if (!enabled) {
        setMsg("OpenBB 当前未启用，请先保存 OPENBB_ENABLED=true。");
      } else {
        setMsg(`OpenBB 已启用但连接失败（${base}），请确认服务是否启动。`);
      }
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setTestingOpenbb(false);
    }
  };

  const saveRisk = async () => {
    if (!risk) return;
    try {
      await apiPost("/setup/risk-config", risk);
      setMsg("风控参数已保存。");
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  const startServices = async () => {
    setStarting(true);
    try {
      await apiPost("/setup/services/start", { start_feishu_bot: true, enable_auto_trader: true });
      setMsg("服务启动命令已发送。");
      await load();
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setStarting(false);
    }
  };

  const stopAllServices = async () => {
    const ok = confirm("确认停止前端和后端服务吗？这会关闭当前页面连接。");
    if (!ok) return;
    setStoppingAll(true);
    try {
      await apiPost("/setup/services/stop-all", {
        stop_backend: true,
        stop_frontend: true,
        stop_feishu_bot: true,
        stop_auto_trader: true,
      });
      setMsg("停止命令已发送，页面可能即将断开。");
      setErr("");
      // 不调用 load()，因为后端会自停，避免无意义报错闪烁。
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setStoppingAll(false);
    }
  };

  const saveFeeSchedule = async () => {
    setSavingFees(true);
    try {
      const parsed = feeAdvancedMode
        ? JSON.parse(feeScheduleText || "{}")
        : feeFormToSchedulePatch(feeForm);
      const resp = await apiPost<FeeScheduleResponse>("/fees/schedule", { schedule: parsed });
      setMsg("费用模型已保存。");
      setErr("");
      setFeeScheduleText(JSON.stringify(resp?.schedule || {}, null, 2));
      setFeeForm(scheduleToFeeForm(resp?.schedule || {}));
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setSavingFees(false);
    }
  };

  const resetFeeScheduleToDefault = async () => {
    setSavingFees(true);
    try {
      const def = await apiGet<FeeScheduleResponse>("/fees/schedule/default");
      const resp = await apiPost<FeeScheduleResponse>("/fees/schedule", {
        schedule: def?.schedule || {},
      });
      setFeeScheduleText(JSON.stringify(resp?.schedule || {}, null, 2));
      setFeeForm(scheduleToFeeForm(resp?.schedule || {}));
      setMsg("已恢复默认费用模型并保存。");
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setSavingFees(false);
    }
  };

  const runFeeEstimate = async () => {
    try {
      const params = new URLSearchParams({
        asset_class: feeEstimateForm.asset_class,
        market: feeEstimateForm.market,
        side: feeEstimateForm.side,
        quantity: String(Math.max(1, Number(feeEstimateForm.quantity) || 1)),
        price: String(Math.max(0, Number(feeEstimateForm.price) || 0)),
      });
      const est = await apiGet<any>(`/fees/estimate?${params.toString()}`);
      setFeeEstimate(est);
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  const probeLongPort = async () => {
    try {
      const dg = await apiGet<LongPortDiag>("/setup/longport/diagnostics?probe=true");
      setDiag(dg);
      setErr("");
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">首次配置向导</h1>
            <div className="mt-1 text-sm text-slate-300">填写秘钥 → 保存配置 → 启动服务</div>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="tag-muted">LongPort {statusLoading ? "检测中..." : status?.configured.longport ? "已配置" : "未配置"}</span>
            <span className="tag-muted">Feishu {statusLoading ? "检测中..." : status?.configured.feishu ? "已配置" : "未配置"}</span>
            <span className="tag-muted">OpenBB {statusLoading ? "检测中..." : status?.configured.openbb ? "已启用" : "未启用"}</span>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-4 text-sm">
          <div className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
            LongPort：{statusLoading ? <span className="text-slate-300">检测中...</span> : status?.configured.longport ? <span className="text-emerald-300">已配置</span> : <span className="text-rose-300">未配置</span>}
          </div>
          <div className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
            Feishu：{statusLoading ? <span className="text-slate-300">检测中...</span> : status?.configured.feishu ? <span className="text-emerald-300">已配置</span> : <span className="text-rose-300">未配置</span>}
          </div>
          <div className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
            行情API：{statusLoading ? <span className="text-slate-300">检测中...</span> : status?.configured.market_apis ? <span className="text-emerald-300">已配置</span> : <span className="text-slate-300">可选</span>}
          </div>
          <div className="rounded-lg border border-slate-700/70 bg-slate-900/70 px-3 py-2">
            OpenBB：{statusLoading ? <span className="text-slate-300">检测中...</span> : status?.configured.openbb ? <span className="text-emerald-300">已启用</span> : <span className="text-slate-300">未启用</span>}
          </div>
        </div>
      </div>

      <div className="panel space-y-3">
        <div className="field-label">LongPort 连接诊断（可视化）</div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-300">估算总连接占用（API + MCP + Feishu）</span>
            <span className={(diag?.estimated_connections_total || 0) >= 8 ? "text-rose-300" : (diag?.estimated_connections_total || 0) >= 5 ? "text-amber-300" : "text-emerald-300"}>
              {diag?.estimated_connections_total ?? 0}/{diag?.connection_limit ?? 10}
            </span>
          </div>
          <div className="mt-2 h-2 w-full rounded bg-slate-800">
            <div
              className={`h-2 rounded ${((diag?.estimated_usage_pct_total || 0) >= 80 ? "bg-rose-500" : (diag?.estimated_usage_pct_total || 0) >= 50 ? "bg-amber-500" : "bg-emerald-500")}`}
              style={{ width: `${Math.max(0, Math.min(100, diag?.estimated_usage_pct_total || 0))}%` }}
            />
          </div>
          <div className="mt-2 text-xs text-slate-400">
            API: {diag?.estimated_breakdown?.api_active ?? 0} | MCP: {diag?.estimated_breakdown?.mcp_estimated ?? 0} | Feishu: {diag?.estimated_breakdown?.feishu_estimated ?? 0}
          </div>
          {(diag?.alert_level && diag.alert_level !== "ok") ? (
            <div className={`mt-2 rounded border px-2 py-1 text-xs ${
              diag.alert_level === "critical"
                ? "border-rose-500/50 bg-rose-950/30 text-rose-300"
                : diag.alert_level === "warning"
                  ? "border-amber-500/50 bg-amber-950/20 text-amber-300"
                  : "border-cyan-500/40 bg-cyan-950/20 text-cyan-300"
            }`}>
              连接占用告警：{diag.alert_level === "critical" ? "严重" : diag.alert_level === "warning" ? "偏高" : "注意"}
            </div>
          ) : null}
          <div className="mt-3 border-t border-slate-800 pt-3" />
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-300">当前 API 进程连接占用</span>
            <span className={diag?.active_connections_api_process ? "text-amber-300" : "text-emerald-300"}>
              {diag?.active_connections_api_process ?? 0}/{diag?.connection_limit ?? 10}
            </span>
          </div>
          <div className="mt-2 h-2 w-full rounded bg-slate-800">
            <div
              className={`h-2 rounded ${((diag?.usage_pct_api_process || 0) >= 80 ? "bg-rose-500" : (diag?.usage_pct_api_process || 0) >= 50 ? "bg-amber-500" : "bg-emerald-500")}`}
              style={{ width: `${Math.max(0, Math.min(100, diag?.usage_pct_api_process || 0))}%` }}
            />
          </div>
          <div className="mt-2 text-xs text-slate-400">
            QuoteCtx: {diag?.quote_ctx_ready ? "已建立" : "未建立"} | TradeCtx: {diag?.trade_ctx_ready ? "已建立" : "未建立"}
          </div>
          <div className="mt-1 text-xs text-slate-500">
            进程状态：API {diag?.processes?.api?.running ? "运行" : "未运行"} / MCP {diag?.processes?.mcp?.running ? "运行" : "未运行"} / Feishu {diag?.processes?.feishu_bot?.running ? "运行" : "未运行"}
          </div>
          {(diag?.recommendations || []).length ? (
            <div className="mt-2 rounded border border-slate-700/70 bg-slate-950/50 p-2 text-xs text-slate-300">
              <div className="mb-1 text-slate-200">建议操作</div>
              {(diag?.recommendations || []).map((x, i) => (
                <div key={`${i}-${x}`}>- {x}</div>
              ))}
            </div>
          ) : null}
          {diag?.last_error ? <div className="mt-1 text-xs text-rose-300">最近错误：{diag.last_error}</div> : null}
          <div className="mt-1 text-xs text-slate-500">{diag?.note || "暂无诊断信息"}</div>
          <button className="btn-secondary mt-3" onClick={probeLongPort}>立即探测连接</button>
        </div>
      </div>

      {msg ? <div className="panel border-emerald-200 bg-emerald-50 text-emerald-700">{msg}</div> : null}
      {err ? <div className="panel border-rose-200 bg-rose-50 text-rose-700">{err}</div> : null}

      <div className="panel space-y-3">
        <div className="field-label">LongPort & Feishu（仅填写要更新的值）</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <input className="input-base" type="password" placeholder={`LONGPORT_APP_KEY (当前: ${status?.values.longport_app_key || "未配置"})`} value={form.longport_app_key} onChange={(e) => setForm((s) => ({ ...s, longport_app_key: e.target.value }))} />
          <input className="input-base" type="password" placeholder={`LONGPORT_APP_SECRET (当前: ${status?.values.longport_app_secret || "未配置"})`} value={form.longport_app_secret} onChange={(e) => setForm((s) => ({ ...s, longport_app_secret: e.target.value }))} />
          <input className="input-base" type="password" placeholder={`LONGPORT_ACCESS_TOKEN (当前: ${status?.values.longport_access_token || "未配置"})`} value={form.longport_access_token} onChange={(e) => setForm((s) => ({ ...s, longport_access_token: e.target.value }))} />
          <input className="input-base" type="password" placeholder={`FEISHU_APP_ID (当前: ${status?.values.feishu_app_id || "未配置"})`} value={form.feishu_app_id} onChange={(e) => setForm((s) => ({ ...s, feishu_app_id: e.target.value }))} />
          <input className="input-base" type="password" placeholder={`FEISHU_APP_SECRET (当前: ${status?.values.feishu_app_secret || "未配置"})`} value={form.feishu_app_secret} onChange={(e) => setForm((s) => ({ ...s, feishu_app_secret: e.target.value }))} />
          <input className="input-base" placeholder={`FEISHU_SCHEDULED_CHAT_ID (当前: ${status?.values.feishu_scheduled_chat_id || "未配置"})`} value={form.feishu_scheduled_chat_id} onChange={(e) => setForm((s) => ({ ...s, feishu_scheduled_chat_id: e.target.value }))} />
        </div>
      </div>

      <div className="panel space-y-3">
        <div className="field-label">OpenClaw MCP 工具分级授权（L1/L2/L3）</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <select
            className="input-base"
            value={form.openclaw_mcp_max_level}
            onChange={(e) => setForm((s) => ({ ...s, openclaw_mcp_max_level: e.target.value }))}
          >
            <option value="">OPENCLAW_MCP_MAX_LEVEL (当前: {status?.values.openclaw_mcp_max_level || "L2"})</option>
            <option value="L1">L1</option>
            <option value="L2">L2</option>
            <option value="L3">L3</option>
          </select>
          <select
            className="input-base"
            value={form.openclaw_mcp_allow_l3}
            onChange={(e) => setForm((s) => ({ ...s, openclaw_mcp_allow_l3: e.target.value }))}
          >
            <option value="">OPENCLAW_MCP_ALLOW_L3 (当前: {status?.values.openclaw_mcp_allow_l3 || "false"})</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <input
            className="input-base"
            type="password"
            placeholder={`OPENCLAW_MCP_L3_CONFIRMATION_TOKEN (当前: ${status?.values.openclaw_mcp_l3_confirmation_token || "未配置"})`}
            value={form.openclaw_mcp_l3_confirmation_token}
            onChange={(e) => setForm((s) => ({ ...s, openclaw_mcp_l3_confirmation_token: e.target.value }))}
          />
        </div>
        <div className="text-xs text-slate-400">
          提示：L3 需要同时满足 MAX_LEVEL=L3、ALLOW_L3=true，并在调用时提供 confirmation_token。
        </div>
      </div>

      <div className="panel space-y-3">
        <div className="field-label">OpenBB 外部研究源（可选）</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <select
            className="input-base"
            value={form.openbb_enabled}
            onChange={(e) => setForm((s) => ({ ...s, openbb_enabled: e.target.value }))}
          >
            <option value="">OPENBB_ENABLED (当前: {status?.values.openbb_enabled || "false"})</option>
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
          <input
            className="input-base"
            placeholder={`OPENBB_BASE_URL (当前: ${status?.values.openbb_base_url || "http://127.0.0.1:6900"})`}
            value={form.openbb_base_url}
            onChange={(e) => setForm((s) => ({ ...s, openbb_base_url: e.target.value }))}
          />
          <input
            className="input-base"
            placeholder={`OPENBB_TIMEOUT_SECONDS (当前: ${status?.values.openbb_timeout_seconds || "8"})`}
            value={form.openbb_timeout_seconds}
            onChange={(e) => setForm((s) => ({ ...s, openbb_timeout_seconds: e.target.value }))}
          />
        </div>
        <div className="text-xs text-slate-400">
          建议先启动 OpenBB API（默认 http://127.0.0.1:6900），再点击“测试 OpenBB 连接”。
        </div>
        <button className="btn-secondary" onClick={testOpenbb} disabled={testingOpenbb}>
          {testingOpenbb ? "测试中..." : "测试 OpenBB 连接"}
        </button>
      </div>

      <div className="panel space-y-3">
        <div className="field-label">扩展行情 API Key（可选）</div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <input className="input-base" type="password" placeholder={`FINNHUB_API_KEY (当前: ${status?.values.finnhub_api_key || "未配置"})`} value={form.finnhub_api_key} onChange={(e) => setForm((s) => ({ ...s, finnhub_api_key: e.target.value }))} />
          <input className="input-base" type="password" placeholder={`TIINGO_API_KEY (当前: ${status?.values.tiingo_api_key || "未配置"})`} value={form.tiingo_api_key} onChange={(e) => setForm((s) => ({ ...s, tiingo_api_key: e.target.value }))} />
          <input className="input-base" type="password" placeholder={`FRED_API_KEY (当前: ${status?.values.fred_api_key || "未配置"})`} value={form.fred_api_key} onChange={(e) => setForm((s) => ({ ...s, fred_api_key: e.target.value }))} />
          <input className="input-base" type="password" placeholder={`COINGECKO_API_KEY (当前: ${status?.values.coingecko_api_key || "未配置"})`} value={form.coingecko_api_key} onChange={(e) => setForm((s) => ({ ...s, coingecko_api_key: e.target.value }))} />
        </div>
        <button className="btn-primary" onClick={saveSecrets} disabled={saving}>
          {saving ? "保存中..." : "保存秘钥到 .env"}
        </button>
      </div>

      <div className="panel space-y-3">
        <div className="field-label">交易费用模型（港股/美股/美股期权）</div>
        <div className="text-xs text-slate-400">
          默认使用表单编辑（适合非技术用户）；保存后将用于 `/fees/estimate` 与回测成本估算。
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn-secondary" onClick={() => setFeeAdvancedMode((x) => !x)}>
            {feeAdvancedMode ? "切换到表单模式" : "切换到高级JSON模式"}
          </button>
          <button className="btn-secondary" onClick={resetFeeScheduleToDefault} disabled={savingFees}>
            恢复默认费率
          </button>
        </div>
        {!feeAdvancedMode ? (
          <div className="space-y-4">
            <div className="rounded border border-slate-700/70 p-3">
              <div className="mb-2 text-sm text-slate-200">港股（股票）</div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                <label className="text-xs text-slate-300">佣金启用
                  <select className="input-base mt-1" value={feeForm.hk_commission_enabled ? "1" : "0"} onChange={(e) => setFeeForm((s) => ({ ...s, hk_commission_enabled: e.target.value === "1" }))}>
                    <option value="0">否（免佣）</option><option value="1">是</option>
                  </select>
                </label>
                <label className="text-xs text-slate-300">佣金费率(%)
                  <input className="input-base mt-1" type="number" step="0.0001" value={feeForm.hk_commission_rate_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_commission_rate_pct: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">佣金最低(HKD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.hk_commission_min} onChange={(e) => setFeeForm((s) => ({ ...s, hk_commission_min: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">平台费(HKD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.hk_platform_fee} onChange={(e) => setFeeForm((s) => ({ ...s, hk_platform_fee: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">印花税(%)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.hk_stamp_duty_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_stamp_duty_pct: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">交易费(%)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.hk_trading_fee_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_trading_fee_pct: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">交易征费(%)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.hk_sfc_levy_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_sfc_levy_pct: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">会财局征费(%)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.hk_afrc_levy_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_afrc_levy_pct: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">交收费(%)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.hk_ccass_fee_pct} onChange={(e) => setFeeForm((s) => ({ ...s, hk_ccass_fee_pct: feeNum(e.target.value) }))} />
                </label>
              </div>
            </div>
            <div className="rounded border border-slate-700/70 p-3">
              <div className="mb-2 text-sm text-slate-200">美股（股票）</div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                <label className="text-xs text-slate-300">平台费(USD/股)
                  <input className="input-base mt-1" type="number" step="0.000001" value={feeForm.us_platform_per_share} onChange={(e) => setFeeForm((s) => ({ ...s, us_platform_per_share: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">平台费最低(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_platform_min} onChange={(e) => setFeeForm((s) => ({ ...s, us_platform_min: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">平台费最高(%成交额)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_platform_max_pct_notional} onChange={(e) => setFeeForm((s) => ({ ...s, us_platform_max_pct_notional: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">交收费(USD/股)
                  <input className="input-base mt-1" type="number" step="0.000001" value={feeForm.us_settlement_per_share} onChange={(e) => setFeeForm((s) => ({ ...s, us_settlement_per_share: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">交收费最高(%成交额)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_settlement_max_pct_notional} onChange={(e) => setFeeForm((s) => ({ ...s, us_settlement_max_pct_notional: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">TAF(USD/股,卖出)
                  <input className="input-base mt-1" type="number" step="0.000001" value={feeForm.us_taf_per_share} onChange={(e) => setFeeForm((s) => ({ ...s, us_taf_per_share: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">TAF最低(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_taf_min} onChange={(e) => setFeeForm((s) => ({ ...s, us_taf_min: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">TAF最高(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_taf_max} onChange={(e) => setFeeForm((s) => ({ ...s, us_taf_max: feeNum(e.target.value) }))} />
                </label>
              </div>
            </div>
            <div className="rounded border border-slate-700/70 p-3">
              <div className="mb-2 text-sm text-slate-200">美股期权（普通订单）</div>
              <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                <label className="text-xs text-slate-300">佣金(USD/张)
                  <input className="input-base mt-1" type="number" step="0.0001" value={feeForm.us_option_commission_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_commission_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">佣金最低(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_option_commission_min} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_commission_min: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">平台费(USD/张)
                  <input className="input-base mt-1" type="number" step="0.0001" value={feeForm.us_option_platform_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_platform_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">平台费最低(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_option_platform_min} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_platform_min: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">期权交收费(USD/张)
                  <input className="input-base mt-1" type="number" step="0.0001" value={feeForm.us_option_settlement_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_settlement_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">期权监管费(USD/张)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.us_option_regulatory_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_regulatory_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">期权清算费(USD/张)
                  <input className="input-base mt-1" type="number" step="0.0001" value={feeForm.us_option_clearing_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_clearing_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">期权TAF(USD/张,卖出)
                  <input className="input-base mt-1" type="number" step="0.00001" value={feeForm.us_option_taf_per_contract} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_taf_per_contract: feeNum(e.target.value) }))} />
                </label>
                <label className="text-xs text-slate-300">期权TAF最低(USD/笔)
                  <input className="input-base mt-1" type="number" step="0.01" value={feeForm.us_option_taf_min} onChange={(e) => setFeeForm((s) => ({ ...s, us_option_taf_min: feeNum(e.target.value) }))} />
                </label>
              </div>
            </div>
          </div>
        ) : (
          <textarea
            className="input-base min-h-[280px] w-full font-mono text-xs"
            value={feeScheduleText}
            onChange={(e) => setFeeScheduleText(e.target.value)}
          />
        )}
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={load}>从后端重新加载</button>
          <button className="btn-primary" onClick={saveFeeSchedule} disabled={savingFees}>
            {savingFees ? "保存中..." : "保存费用模型"}
          </button>
        </div>
        <div className="rounded border border-slate-700/70 p-3">
          <div className="mb-2 text-sm text-slate-200">费用试算</div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
            <select className="input-base" value={feeEstimateForm.asset_class} onChange={(e) => setFeeEstimateForm((s) => ({ ...s, asset_class: e.target.value as "stock" | "us_option" }))}>
              <option value="stock">股票</option>
              <option value="us_option">美股期权</option>
            </select>
            <select className="input-base" value={feeEstimateForm.market} onChange={(e) => setFeeEstimateForm((s) => ({ ...s, market: e.target.value as "HK" | "US" | "CN" | "OTHER" }))} disabled={feeEstimateForm.asset_class !== "stock"}>
              <option value="US">US</option><option value="HK">HK</option><option value="CN">CN</option><option value="OTHER">OTHER</option>
            </select>
            <select className="input-base" value={feeEstimateForm.side} onChange={(e) => setFeeEstimateForm((s) => ({ ...s, side: e.target.value as "buy" | "sell" }))}>
              <option value="buy">买入</option><option value="sell">卖出</option>
            </select>
            <input className="input-base" type="number" value={feeEstimateForm.quantity} onChange={(e) => setFeeEstimateForm((s) => ({ ...s, quantity: Number(e.target.value) }))} placeholder="数量/张数" />
            <input className="input-base" type="number" step="0.0001" value={feeEstimateForm.price} onChange={(e) => setFeeEstimateForm((s) => ({ ...s, price: Number(e.target.value) }))} placeholder="价格" disabled={feeEstimateForm.asset_class !== "stock"} />
          </div>
          <div className="mt-2 flex gap-2">
            <button className="btn-secondary" onClick={runFeeEstimate}>立即试算</button>
          </div>
          {feeEstimate ? (
            <div className="mt-2 space-y-2">
              <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
                <div className="rounded border border-slate-700/70 bg-slate-950/50 p-2 text-xs text-slate-300">
                  <div className="text-slate-400">总费用</div>
                  <div className="mt-1 text-sm text-emerald-300">{fmtNum(feeEstimate?.estimate?.total_fee, 6)}</div>
                </div>
                <div className="rounded border border-slate-700/70 bg-slate-950/50 p-2 text-xs text-slate-300">
                  <div className="text-slate-400">成交额</div>
                  <div className="mt-1 text-sm">{feeEstimate?.estimate?.notional !== undefined ? fmtNum(feeEstimate?.estimate?.notional, 4) : "-"}</div>
                </div>
                <div className="rounded border border-slate-700/70 bg-slate-950/50 p-2 text-xs text-slate-300">
                  <div className="text-slate-400">费用占成交额</div>
                  <div className="mt-1 text-sm">
                    {Number(feeEstimate?.estimate?.notional || 0) > 0
                      ? `${((Number(feeEstimate?.estimate?.total_fee || 0) / Number(feeEstimate?.estimate?.notional || 1)) * 100).toFixed(4)}%`
                      : "-"}
                  </div>
                </div>
                <div className="rounded border border-slate-700/70 bg-slate-950/50 p-2 text-xs text-slate-300">
                  <div className="text-slate-400">资产类型</div>
                  <div className="mt-1 text-sm">{feeEstimate?.asset_class || "-"}</div>
                </div>
              </div>
              <div className="table-shell">
                <table className="min-w-full text-xs">
                  <thead className="table-head">
                    <tr className="text-left">
                      <th className="px-3 py-2">费用项</th>
                      <th className="px-3 py-2">金额</th>
                      <th className="px-3 py-2">占总费用%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(feeEstimate?.estimate?.components || {}).map(([k, v]: [string, any]) => {
                      const total = Number(feeEstimate?.estimate?.total_fee || 0);
                      const amt = Number(v || 0);
                      return (
                        <tr key={k} className="border-t border-slate-800/90">
                          <td className="px-3 py-2 text-slate-300">{k}</td>
                          <td className="px-3 py-2 text-slate-200">{fmtNum(amt, 6)}</td>
                          <td className="px-3 py-2 text-slate-400">{total > 0 ? `${(amt / total * 100).toFixed(2)}%` : "-"}</td>
                        </tr>
                      );
                    })}
                    {feeEstimate?.estimate?.stamp_duty ? (
                      <tr className="border-t border-slate-800/90">
                        <td className="px-3 py-2 text-slate-300">stamp_duty</td>
                        <td className="px-3 py-2 text-slate-200">{fmtNum(feeEstimate?.estimate?.stamp_duty, 6)}</td>
                        <td className="px-3 py-2 text-slate-400">
                          {Number(feeEstimate?.estimate?.total_fee || 0) > 0
                            ? `${(Number(feeEstimate?.estimate?.stamp_duty || 0) / Number(feeEstimate?.estimate?.total_fee || 1) * 100).toFixed(2)}%`
                            : "-"}
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div className="panel space-y-3">
        <div className="field-label">风控参数</div>
        {risk ? (
          <>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
              <input className="input-base" type="number" value={risk.max_order_amount} onChange={(e) => setRisk({ ...risk, max_order_amount: Number(e.target.value) })} />
              <input className="input-base" type="number" step="0.01" value={risk.max_daily_loss_pct} onChange={(e) => setRisk({ ...risk, max_daily_loss_pct: Number(e.target.value) })} />
              <input className="input-base" type="number" step="0.01" value={risk.stop_loss_pct} onChange={(e) => setRisk({ ...risk, stop_loss_pct: Number(e.target.value) })} />
              <input className="input-base" type="number" step="0.01" value={risk.max_position_pct} onChange={(e) => setRisk({ ...risk, max_position_pct: Number(e.target.value) })} />
              <select className="input-base" value={risk.enabled ? "1" : "0"} onChange={(e) => setRisk({ ...risk, enabled: e.target.value === "1" })}>
                <option value="1">风控启用</option>
                <option value="0">风控关闭</option>
              </select>
            </div>
            <button className="btn-secondary" onClick={saveRisk}>保存风控参数</button>
          </>
        ) : (
          <div className="text-sm text-slate-400">加载中...</div>
        )}
      </div>

      <div className="panel space-y-2">
        <div className="field-label">服务启动</div>
        <div className="text-sm text-slate-300">
          Feishu Bot：{services?.feishu_bot_running ? <span className="text-emerald-300">运行中</span> : <span className="text-slate-300">未运行</span>}
          {" | "}
          Auto Trader：{services?.auto_trader_scheduler_running ? <span className="text-emerald-300">运行中</span> : <span className="text-slate-300">未运行</span>}
        </div>
        <button className="btn-primary" onClick={startServices} disabled={starting}>
          {starting ? "启动中..." : "一键启动服务"}
        </button>
        <button className="btn-secondary" onClick={stopAllServices} disabled={stoppingAll}>
          {stoppingAll ? "停止中..." : "停止前后端服务"}
        </button>
      </div>
    </PageShell>
  );
}

