"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPost, apiPut } from "@/lib/api";
import { PageShell } from "@/components/ui/page-shell";

const SIGNALS_PAGE_STORAGE_KEY = "signals_page_symbols_v1";

const INPUT_CLS =
  "w-full rounded-lg border border-slate-600 bg-slate-950/70 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-500/60";

const BUILTIN_REVERSAL_CONDITIONS = [
  {
    id: "rsi_rebound",
    label: "RSI 超卖反转",
    desc: "RSI 从超卖区回升，或仍处超卖区提示关注。",
  },
  {
    id: "macd_bullish_cross_below_zero",
    label: "MACD 零轴下方金叉",
    desc: "DIF 在零轴下方上穿信号线。",
  },
  {
    id: "bollinger_rebound",
    label: "布林带下轨反弹",
    desc: "K线触及下轨后回到下轨上方。",
  },
  {
    id: "hammer_candle",
    label: "锤子线形态",
    desc: "长下影、短上影的潜在止跌形态。",
  },
  {
    id: "volume_rebound",
    label: "放量反弹",
    desc: "价格反弹并伴随明显放量。",
  },
  {
    id: "ma5_cross_ma20",
    label: "MA5 上穿 MA20",
    desc: "短均线上穿中期均线的金叉。",
  },
] as const;

function clonePrefs(p: unknown) {
  return JSON.parse(JSON.stringify(p ?? {}));
}

export default function NotificationsPage() {
  const [data, setData] = useState<any>(null);
  const [serviceStatus, setServiceStatus] = useState<any>(null);
  const [prefDraft, setPrefDraft] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [savingPrefs, setSavingPrefs] = useState(false);
  const [message, setMessage] = useState("");
  const [showBuiltinReversalConfig, setShowBuiltinReversalConfig] = useState(false);
  const loadingRef = useRef(false);

  const loadStatus = useCallback(async () => {
    if (loadingRef.current || (typeof document !== "undefined" && document.hidden)) return;
    loadingRef.current = true;
    try {
      const [d, s] = await Promise.all([apiGet<any>("/notifications/status"), apiGet<any>("/setup/services/status")]);
      setData(d);
      setServiceStatus(s);
      setError("");
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      loadingRef.current = false;
    }
  }, []);

  const loadPreferences = useCallback(async () => {
    try {
      const pr = await apiGet<any>("/notifications/preferences");
      if (pr?.preferences) {
        setPrefDraft(clonePrefs(pr.preferences));
      }
    } catch (e: any) {
      setError(String(e.message || e));
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    void loadPreferences();
    const t = setInterval(() => void loadStatus(), 20000);
    return () => clearInterval(t);
  }, [loadStatus, loadPreferences]);

  const startFeishuBot = async () => {
    setLoading(true);
    try {
      await apiPost("/setup/services/start", { start_feishu_bot: true, enable_auto_trader: false });
      setMessage("飞书机器人已启动");
      await loadStatus();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
      setTimeout(() => setMessage(""), 3000);
    }
  };

  const stopFeishuBot = async () => {
    setLoading(true);
    try {
      await apiPost("/setup/services/stop", { stop_feishu_bot: true, stop_auto_trader: false });
      setMessage("飞书机器人已停止");
      await loadStatus();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
      setTimeout(() => setMessage(""), 3000);
    }
  };

  const startAutoTrader = async () => {
    setLoading(true);
    try {
      await apiPost("/setup/services/start", { start_feishu_bot: false, enable_auto_trader: true });
      setMessage("自动交易已启动");
      await loadStatus();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
      setTimeout(() => setMessage(""), 3000);
    }
  };

  const stopAutoTrader = async () => {
    setLoading(true);
    try {
      await apiPost("/setup/services/stop", { stop_feishu_bot: false, stop_auto_trader: true });
      setMessage("自动交易已停止");
      await loadStatus();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
      setTimeout(() => setMessage(""), 3000);
    }
  };

  const importSymbolsFromSignalsPage = () => {
    try {
      const raw = localStorage.getItem(SIGNALS_PAGE_STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      const list = Array.isArray(parsed)
        ? parsed.map((x: string) => String(x ?? "").trim().toUpperCase()).filter(Boolean)
        : [];
      setPrefDraft((prev: any) => ({
        ...prev,
        bottom_reversal_watch: {
          ...(prev?.bottom_reversal_watch || {}),
          symbols: list.slice(0, 30),
        },
      }));
      setMessage(`已从信号中心导入 ${list.length} 个标的（本地缓存）`);
      setTimeout(() => setMessage(""), 4000);
    } catch {
      setError("读取信号中心本地列表失败");
    }
  };

  const savePreferences = async () => {
    if (!prefDraft) return;
    setSavingPrefs(true);
    try {
      await apiPut("/notifications/preferences", prefDraft);
      setMessage("通知偏好已保存");
      await loadPreferences();
      await loadStatus();
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setSavingPrefs(false);
      setTimeout(() => setMessage(""), 3000);
    }
  };

  const br = prefDraft?.bottom_reversal_watch || {};
  const fbm = prefDraft?.feishu_builtin_reversal_monitor || {};
  const fbmMode = fbm?.selection_mode === "single" ? "single" : "multi";
  const fbmSelected = Array.isArray(fbm?.selected_conditions)
    ? fbm.selected_conditions.map((x: unknown) => String(x))
    : BUILTIN_REVERSAL_CONDITIONS.map((x) => x.id);
  const symbolsText = Array.isArray(br.symbols) ? br.symbols.join("\n") : "";

  return (
    <PageShell>
      <div className="panel border-cyan-500/20 bg-gradient-to-br from-slate-900/95 via-slate-900/95 to-indigo-950/30">
        <div className="page-header">
          <div>
            <h1 className="page-title">通知中心</h1>
            <div className="mt-1 text-sm text-slate-300">
              服务启停 · 推送状态 · 自动交易调度 · 通知偏好（读写）
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <span className="tag-muted">飞书机器人 {serviceStatus?.feishu_bot_running ? "运行中" : "已停止"}</span>
            <span className="tag-muted">自动调度 {serviceStatus?.auto_trader_scheduler_running ? "运行中" : "已停止"}</span>
          </div>
        </div>
      </div>

      {error ? <div className="panel border-rose-500/40 bg-rose-950/40 text-rose-200">{error}</div> : null}
      {message ? <div className="panel border-emerald-500/40 bg-emerald-950/30 text-emerald-200">{message}</div> : null}

      {data && serviceStatus ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="panel space-y-3 border-slate-700/80 bg-slate-900/40">
            <div className="section-title text-slate-200">飞书配置</div>
            <div className="text-sm text-slate-300">
              飞书应用:
              <span className={data.feishu_app_configured ? "ml-2 text-emerald-400" : "ml-2 text-rose-400"}>
                {data.feishu_app_configured ? "已配置" : "未配置"}
              </span>
            </div>
            <div className="text-sm text-slate-300">飞书机器人数量: {data.feishu_bots_count ?? 0}</div>

            <div className="border-t border-slate-700 pt-3">
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="text-slate-200">飞书指令机器人:</span>
                <span className={serviceStatus.feishu_bot_running ? "text-emerald-400" : "text-slate-500"}>
                  {serviceStatus.feishu_bot_running ? "运行中" : "已停止"}
                </span>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={startFeishuBot}
                  disabled={loading || serviceStatus.feishu_bot_running}
                  className="btn-primary flex-1 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  开启
                </button>
                <button
                  onClick={stopFeishuBot}
                  disabled={loading || !serviceStatus.feishu_bot_running}
                  className="btn-secondary flex-1 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  关闭
                </button>
              </div>
            </div>
          </div>

          <div className="panel space-y-3 border-slate-700/80 bg-slate-900/40">
            <div className="section-title text-slate-200">定时推送</div>
            <div className="text-sm text-slate-300">推送目标群 ID:</div>
            <div className="break-all rounded-lg border border-slate-600 bg-slate-950/60 px-3 py-2 font-mono text-xs text-slate-300">
              {data.scheduled_chat_id || "未配置"}
            </div>
            <p className="text-xs text-slate-500">
              需配置 scheduled_chat_id；整点市场报告是否发送由下方「定时市场分析报告」开关控制（保存后飞书机器人下次整点前会按新配置生效）。
            </p>

            <div className="border-t border-slate-700 pt-3">
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="text-slate-200">自动交易调度器:</span>
                <span className={serviceStatus.auto_trader_scheduler_running ? "text-emerald-400" : "text-slate-500"}>
                  {serviceStatus.auto_trader_scheduler_running ? "运行中" : "已停止"}
                </span>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={startAutoTrader}
                  disabled={loading || serviceStatus.auto_trader_scheduler_running}
                  className="btn-primary flex-1 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  开启
                </button>
                <button
                  onClick={stopAutoTrader}
                  disabled={loading || !serviceStatus.auto_trader_scheduler_running}
                  className="btn-secondary flex-1 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="panel border-slate-700 bg-slate-900/40">加载中...</div>
      )}

      <div className="panel mt-4 space-y-4 border-cyan-500/20 bg-slate-900/50">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">通知偏好</h2>
            <p className="mt-1 text-xs text-slate-500">
              写入 <code className="text-cyan-300/90">mcp_server/notification_config.json</code> 中的{" "}
              <code className="text-cyan-300/90">notification_preferences</code>
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void loadPreferences()}
              className="btn-secondary text-sm"
              disabled={savingPrefs}
            >
              重新加载
            </button>
            <button
              type="button"
              onClick={() => void savePreferences()}
              className="btn-primary text-sm"
              disabled={!prefDraft || savingPrefs}
            >
              {savingPrefs ? "保存中…" : "保存偏好"}
            </button>
          </div>
        </div>

        {!prefDraft ? (
          <div className="text-sm text-slate-500">正在加载偏好…</div>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-slate-700 bg-slate-950/40 p-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={!!prefDraft.scheduled_market_report?.enabled}
                onChange={(e) =>
                  setPrefDraft({
                    ...prefDraft,
                    scheduled_market_report: {
                      ...prefDraft.scheduled_market_report,
                      enabled: e.target.checked,
                    },
                  })
                }
              />
              <span>
                <span className="font-medium text-slate-200">定时市场分析报告</span>
                <p className="mt-1 text-xs text-slate-500">{prefDraft.scheduled_market_report?.note}</p>
              </span>
            </label>

            <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-slate-700 bg-slate-950/40 p-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={!!prefDraft.semi_auto_pending_signal?.enabled}
                onChange={(e) =>
                  setPrefDraft({
                    ...prefDraft,
                    semi_auto_pending_signal: {
                      ...prefDraft.semi_auto_pending_signal,
                      enabled: e.target.checked,
                    },
                  })
                }
              />
              <span>
                <span className="font-medium text-slate-200">半自动 · 待确认信号（含买入等）</span>
                <p className="mt-1 text-xs text-slate-500">{prefDraft.semi_auto_pending_signal?.note}</p>
              </span>
            </label>

            <div className="rounded-lg border border-slate-700 bg-slate-950/40 p-3">
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={!!prefDraft.full_auto_execution?.enabled}
                  onChange={(e) =>
                    setPrefDraft({
                      ...prefDraft,
                      full_auto_execution: {
                        ...prefDraft.full_auto_execution,
                        enabled: e.target.checked,
                      },
                    })
                  }
                />
                <span>
                  <span className="font-medium text-slate-200">全自动 · 成交结果通知</span>
                  <p className="mt-1 text-xs text-slate-500">{prefDraft.full_auto_execution?.note}</p>
                </span>
              </label>
              <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  disabled={!prefDraft.full_auto_execution?.enabled}
                  checked={!!prefDraft.full_auto_execution?.notify_on_failure}
                  onChange={(e) =>
                    setPrefDraft({
                      ...prefDraft,
                      full_auto_execution: {
                        ...prefDraft.full_auto_execution,
                        notify_on_failure: e.target.checked,
                      },
                    })
                  }
                />
                失败时也推送
              </label>
            </div>

            <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-slate-700 bg-slate-950/40 p-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={!!prefDraft.observer_mode_digest?.enabled}
                onChange={(e) =>
                  setPrefDraft({
                    ...prefDraft,
                    observer_mode_digest: {
                      ...prefDraft.observer_mode_digest,
                      enabled: e.target.checked,
                    },
                  })
                }
              />
              <span>
                <span className="font-medium text-slate-200">观察模式 · 连续无信号汇总</span>
                <p className="mt-1 text-xs text-slate-500">{prefDraft.observer_mode_digest?.note}</p>
              </span>
            </label>

            <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-950/20 p-3">
              <input
                type="checkbox"
                className="mt-1"
                checked={!!prefDraft.feishu_builtin_reversal_monitor?.enabled}
                onChange={(e) =>
                  setPrefDraft({
                    ...prefDraft,
                    feishu_builtin_reversal_monitor: {
                      ...prefDraft.feishu_builtin_reversal_monitor,
                      enabled: e.target.checked,
                    },
                  })
                }
              />
              <span>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-amber-200/90">飞书机器人 · 内置多条件反转线程</span>
                  <button
                    type="button"
                    className="rounded border border-amber-400/40 px-2 py-0.5 text-[11px] text-amber-200/90 hover:bg-amber-400/10"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setShowBuiltinReversalConfig((v) => !v);
                    }}
                  >
                    {showBuiltinReversalConfig ? "收起条件" : "展开条件"}
                  </button>
                </div>
                <p className="mt-1 text-xs text-slate-500">{prefDraft.feishu_builtin_reversal_monitor?.note}</p>
                <p className="mt-1 text-xs text-amber-200/70">默认关闭；开启后需重启飞书机器人进程。</p>
                {showBuiltinReversalConfig ? (
                  <div className="mt-3 space-y-2 rounded border border-amber-500/30 bg-slate-950/40 p-3">
                    <div className="text-xs text-slate-400">条件选择模式</div>
                    <div className="flex flex-wrap gap-3 text-xs text-slate-300">
                      <label className="inline-flex items-center gap-2">
                        <input
                          type="radio"
                          name="fbm-selection-mode"
                          checked={fbmMode === "multi"}
                          onChange={() =>
                            setPrefDraft({
                              ...prefDraft,
                              feishu_builtin_reversal_monitor: {
                                ...fbm,
                                selection_mode: "multi",
                                selected_conditions:
                                  fbmSelected.length > 0
                                    ? fbmSelected
                                    : BUILTIN_REVERSAL_CONDITIONS.map((x) => x.id),
                              },
                            })
                          }
                        />
                        多选
                      </label>
                      <label className="inline-flex items-center gap-2">
                        <input
                          type="radio"
                          name="fbm-selection-mode"
                          checked={fbmMode === "single"}
                          onChange={() =>
                            setPrefDraft({
                              ...prefDraft,
                              feishu_builtin_reversal_monitor: {
                                ...fbm,
                                selection_mode: "single",
                                selected_conditions: [fbmSelected[0] || BUILTIN_REVERSAL_CONDITIONS[0].id],
                              },
                            })
                          }
                        />
                        单选
                      </label>
                    </div>
                    <div className="space-y-2">
                      {BUILTIN_REVERSAL_CONDITIONS.map((item) => {
                        const checked = fbmSelected.includes(item.id);
                        return (
                          <label key={item.id} className="flex items-start gap-2 rounded border border-slate-700/70 p-2 text-xs">
                            <input
                              type={fbmMode === "single" ? "radio" : "checkbox"}
                              name={fbmMode === "single" ? "fbm-condition-single" : undefined}
                              checked={checked}
                              onChange={(e) => {
                                const on = e.target.checked;
                                const nextSet = new Set(fbmSelected);
                                if (fbmMode === "single") {
                                  setPrefDraft({
                                    ...prefDraft,
                                    feishu_builtin_reversal_monitor: {
                                      ...fbm,
                                      selection_mode: "single",
                                      selected_conditions: [item.id],
                                    },
                                  });
                                  return;
                                }
                                if (on) nextSet.add(item.id);
                                else nextSet.delete(item.id);
                                const next = Array.from(nextSet);
                                setPrefDraft({
                                  ...prefDraft,
                                  feishu_builtin_reversal_monitor: {
                                    ...fbm,
                                    selection_mode: "multi",
                                    selected_conditions: next.length > 0 ? next : [item.id],
                                  },
                                });
                              }}
                            />
                            <span>
                              <span className="text-slate-200">{item.label}</span>
                              <p className="mt-0.5 text-slate-500">{item.desc}</p>
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </span>
            </label>

            <div className="rounded-lg border border-cyan-500/25 bg-slate-950/40 p-3 lg:col-span-2">
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={!!prefDraft.bottom_reversal_watch?.enabled}
                  onChange={(e) =>
                    setPrefDraft({
                      ...prefDraft,
                      bottom_reversal_watch: {
                        ...prefDraft.bottom_reversal_watch,
                        enabled: e.target.checked,
                      },
                    })
                  }
                />
                <span>
                  <span className="font-medium text-cyan-200">API · 底部反转监控（与信号中心同源）</span>
                  <p className="mt-1 text-xs text-slate-500">{prefDraft.bottom_reversal_watch?.note}</p>
                </span>
              </label>

              <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
                <div>
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <span className="text-xs text-slate-400">监控标的（每行一个，最多 30）</span>
                    <button type="button" className="text-xs text-cyan-400 underline" onClick={importSymbolsFromSignalsPage}>
                      从信号中心导入
                    </button>
                  </div>
                  <textarea
                    className={`${INPUT_CLS} min-h-[100px] font-mono text-xs`}
                    value={symbolsText}
                    disabled={!prefDraft.bottom_reversal_watch?.enabled}
                    onChange={(e) => {
                      const lines = e.target.value
                        .split(/\r?\n/)
                        .map((x) => x.trim().toUpperCase())
                        .filter(Boolean);
                      setPrefDraft({
                        ...prefDraft,
                        bottom_reversal_watch: {
                          ...prefDraft.bottom_reversal_watch,
                          symbols: lines.slice(0, 30),
                        },
                      });
                    }}
                    placeholder="AAPL.US&#10;TSLA.US"
                  />
                </div>
                <div className="space-y-2">
                  <div>
                    <span className="text-xs text-slate-400">轮询间隔（秒，60–86400）</span>
                    <input
                      type="number"
                      className={INPUT_CLS}
                      min={60}
                      max={86400}
                      disabled={!prefDraft.bottom_reversal_watch?.enabled}
                      value={Number(prefDraft.bottom_reversal_watch?.poll_interval_seconds ?? 300)}
                      onChange={(e) =>
                        setPrefDraft({
                          ...prefDraft,
                          bottom_reversal_watch: {
                            ...prefDraft.bottom_reversal_watch,
                            poll_interval_seconds: Number(e.target.value) || 300,
                          },
                        })
                      }
                    />
                  </div>
                  <label className="flex items-center gap-2 text-sm text-slate-300">
                    <input
                      type="checkbox"
                      disabled={!prefDraft.bottom_reversal_watch?.enabled}
                      checked={!!prefDraft.bottom_reversal_watch?.only_on_edge}
                      onChange={(e) =>
                        setPrefDraft({
                          ...prefDraft,
                          bottom_reversal_watch: {
                            ...prefDraft.bottom_reversal_watch,
                            only_on_edge: e.target.checked,
                          },
                        })
                      }
                    />
                    仅在「未触发 → 触发」边沿推送
                  </label>
                  <div>
                    <span className="text-xs text-slate-400">同标的冷却（分钟，0 表示不限制）</span>
                    <input
                      type="number"
                      className={INPUT_CLS}
                      min={0}
                      max={10080}
                      disabled={!prefDraft.bottom_reversal_watch?.enabled}
                      value={Number(prefDraft.bottom_reversal_watch?.cooldown_minutes ?? 120)}
                      onChange={(e) =>
                        setPrefDraft({
                          ...prefDraft,
                          bottom_reversal_watch: {
                            ...prefDraft.bottom_reversal_watch,
                            cooldown_minutes: Number(e.target.value) || 0,
                          },
                        })
                      }
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </PageShell>
  );
}
