<div className="panel space-y-3">
  <div className="flex flex-wrap items-center justify-between gap-2">
    <div className={PANEL_TITLE_CLS}>研究中心（P0 / P1 / P2）</div>
    <div className="flex flex-wrap gap-2">
      <button
        className="rounded-lg bg-gradient-to-r from-indigo-600 to-violet-600 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
        onClick={runResearch}
        disabled={researchRunning || !cfg}
      >
        {researchRunning ? `Research 运行中${researchTaskId ? ` (${researchTaskId})` : "..."}` : "一键运行 Research"}
      </button>
      <button
        className="rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
        onClick={runStrategyMatrix}
        disabled={strategyMatrixRunning || !cfg}
      >
        {strategyMatrixRunning ? "矩阵筛选中..." : "策略参数矩阵筛选"}
      </button>
      <button
        className="rounded-lg bg-gradient-to-r from-sky-600 to-blue-700 px-3 py-2 text-sm font-medium text-white shadow hover:opacity-90 disabled:opacity-50"
        onClick={runMlMatrix}
        disabled={mlMatrixRunning || !cfg}
      >
        {mlMatrixRunning ? "ML矩阵运行中..." : "ML参数矩阵筛选"}
      </button>
      <button
        className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm font-medium text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
        onClick={cancelStrategyMatrix}
        disabled={!strategyMatrixRunning || !strategyMatrixTaskId}
      >
        取消矩阵任务
      </button>
      <button
        className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm font-medium text-rose-200 hover:bg-rose-500/20 disabled:opacity-50"
        onClick={cancelMlMatrix}
        disabled={!mlMatrixRunning || !mlMatrixTaskId}
      >
        取消ML矩阵任务
      </button>
      <button
        className="rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-2 text-sm font-medium text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-50"
        onClick={exportResearchSnapshot}
        disabled={!researchSnapshotData}
      >
        导出研究快照 JSON
      </button>
      <button
        className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm font-medium text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-50"
        onClick={exportModelCompareCsv}
        disabled={!modelRows.length}
      >
        导出模型对比 CSV
      </button>
      <button
        className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm font-medium text-amber-200 hover:bg-amber-500/20 disabled:opacity-50"
        onClick={exportPairTradeCsv}
        disabled={!pairTradeRows.length}
      >
        导出组合交易明细 CSV
      </button>
      <button
        className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-2 text-sm font-medium text-violet-200 hover:bg-violet-500/20 disabled:opacity-50"
        onClick={exportAbReportJson}
        disabled={!abReport}
      >
        导出 A/B 报告 JSON
      </button>
      <button
        className="rounded-lg border border-fuchsia-500/40 bg-fuchsia-500/10 px-3 py-2 text-sm font-medium text-fuchsia-200 hover:bg-fuchsia-500/20 disabled:opacity-50"
        onClick={exportAbReportMarkdown}
        disabled={!abMarkdown.trim()}
      >
        导出 A/B 报告 Markdown
      </button>
    </div>
  </div>
  <details className="rounded-lg border border-slate-700/70 bg-slate-900/40 p-3" open>
    <summary className="cursor-pointer text-sm font-medium text-slate-200">研究状态卡</summary>
    <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-6">
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>快照状态</div>
      <div className={`mt-1 text-sm font-semibold ${researchStatus?.has_snapshot ? "text-emerald-300" : "text-slate-300"}`}>
        {researchStatus?.has_snapshot ? "已生成" : "未生成"}
      </div>
    </div>
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>最近生成</div>
      <div className="mt-1 text-sm text-slate-200">{formatTime(researchStatus?.generated_at || undefined)}</div>
    </div>
    <div
      className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3"
      title="与 Auto Trader 当前保存配置一致；下次运行 Research 将使用此项"
    >
      <div className={SUB_TITLE_CLS}>市场/K线（配置）</div>
      <div className="mt-1 text-sm text-cyan-300">
        {(cfg?.market ?? researchStatus?.market ?? "-").toString().toUpperCase()} /{" "}
        {(cfg?.kline ?? researchStatus?.kline ?? "-").toString().toUpperCase()}
      </div>
    </div>
    <div
      className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3"
      title="与 Auto Trader 当前保存配置一致"
    >
      <div className={SUB_TITLE_CLS}>TopN（配置）</div>
      <div className="mt-1 text-sm text-slate-200">{cfg?.top_n ?? researchStatus?.top_n ?? "-"}</div>
    </div>
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>版本号</div>
      <div className="mt-1 truncate text-xs text-slate-300" title={researchStatus?.version || "-"}>
        {researchStatus?.version || "-"}
      </div>
    </div>
    <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3">
      <div className={SUB_TITLE_CLS}>任务队列</div>
      <div className="mt-1 text-sm text-amber-300">
        {researchStatus?.task_queue?.active ?? 0} / {researchStatus?.task_queue?.max_pending ?? "-"}
      </div>
      <div className="mt-1 text-[11px] text-slate-400">
        运行 {researchStatus?.task_queue?.running ?? 0} · 排队 {researchStatus?.task_queue?.queued ?? 0}
      </div>
    </div>
    </div>
    <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-5">
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        数据源：{providerStatus?.primary || "longport"} · OpenBB{" "}
        {providerStatus?.openbb_enabled ? (providerStatus?.openbb_connected ? "已连接" : "未连接") : "未启用"}
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>外部市场状态：{regimeInfo?.available ? regimeInfo?.regime || "unknown" : "-"}</div>
        <div className="mt-1 text-slate-400">
          置信度：{typeof regimeInfo?.confidence === "number" ? regimeInfo.confidence : "-"} | 基准：
          {regimeInfo?.symbol || "-"}
        </div>
        <div className="mt-1 text-slate-400">
          特征：ret20={typeof regimeInfo?.features?.ret_20 === "number" ? regimeInfo.features.ret_20 : "-"} | vol_z=
          {typeof regimeInfo?.features?.vol_z === "number" ? regimeInfo.features.vol_z : "-"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>外部因子样本：{externalFactors.length}</div>
        <div className="mt-1 text-slate-400">
          因子应用：{factorGating?.applied ? "已应用" : "未应用"} ·
          {typeof factorGating?.available_symbols === "number" ? ` ${factorGating.available_symbols}` : " -"} /
          {typeof factorGating?.total_symbols === "number" ? ` ${factorGating.total_symbols}` : " -"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>Regime风控：{regimeGating?.applied ? "已应用" : "未应用"}</div>
        <div className="mt-1 text-slate-400">
          仓位上限：{typeof regimeGating?.effective_exposure === "number" ? `${(regimeGating.effective_exposure * 100).toFixed(1)}%` : "-"}
        </div>
        <div className="mt-1 text-slate-400">
          单标的上限：
          {typeof regimeGating?.max_single_ratio === "number" ? `${(regimeGating.max_single_ratio * 100).toFixed(1)}%` : "-"}
        </div>
      </div>
      <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-xs text-slate-300">
        <div>ML诊断：{mlDiag?.enabled ? "已生成" : "未生成"}</div>
        <div className="mt-1 text-slate-400">
          样本：{mlDiag?.dataset?.samples ?? "-"} | 标签正样本率：
          {typeof mlDiag?.label_distribution?.positive_ratio === "number"
            ? `${(mlDiag.label_distribution.positive_ratio * 100).toFixed(1)}%`
            : "-"}
        </div>
        <div className="mt-1 text-slate-400">
          成本标签(bps)：{mlDiag?.settings?.transaction_cost_bps ?? "-"} | 预测周期：{mlDiag?.settings?.horizon_days ?? "-"}天
        </div>
      </div>
    </div>
  </details>

  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
    <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3" open>
      <summary className="cursor-pointer text-sm font-medium text-slate-200">模型对比榜（P2）</summary>
      {!modelRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无模型对比数据，先执行一次 Research。</div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[520px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">模型</th>
                <th className="py-1">运行次数</th>
                <th className="py-1">平均分</th>
                <th className="py-1">平均Acc</th>
                <th className="py-1">最佳分</th>
              </tr>
            </thead>
            <tbody>
              {modelRows.slice(0, 10).map((x, idx) => (
                <tr key={`${x.model_name || "model"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{x.model_name || "-"}</td>
                  <td className="py-1">{x.runs ?? "-"}</td>
                  <td className="py-1 text-cyan-300">{x.avg_score ?? "-"}</td>
                  <td className="py-1 text-sky-300">{x.avg_accuracy ?? "-"}</td>
                  <td className="py-1 text-emerald-300">{x.best_score ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>

    <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3" open>
      <summary className="cursor-pointer text-sm font-medium text-slate-200">
        策略优选摘要（P1）{matrixTopSymbolRows.length ? " · 已同步矩阵全组合Top5" : ""}
      </summary>
      {!strategySummaryRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无策略摘要数据，先执行一次 Research。</div>
      ) : (
        <div className="mt-2 space-y-1">
          {strategySummaryRows.slice(0, 5).map((row, idx) => {
            const best = row?.best_strategy || {};
            return (
              <div
                key={`${row.symbol || "symbol"}-${idx}`}
                className="rounded border border-slate-800/90 bg-slate-950/40 px-2 py-1 text-xs text-slate-300"
              >
                <span className="text-cyan-300">{row.symbol || "-"}</span>
                {" | "}
                <span>{best.strategy_label || best.strategy || "-"}</span>
                {" | "}
                <span className="text-emerald-300">score: {best.composite_score ?? "-"}</span>
              </div>
            );
          })}
        </div>
      )}
    </details>
  </div>

  <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3" open>
    <summary className="cursor-pointer text-sm font-medium text-slate-200">策略参数矩阵（优秀策略筛选）</summary>
    {strategyMatrixRunning && strategyMatrixTaskId ? (
      <div className="mt-2 text-xs text-amber-300">任务运行中：{strategyMatrixTaskId}</div>
    ) : null}
    {!matrixRows.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无矩阵结果，点击“策略参数矩阵筛选”开始。</div>
    ) : (
      <div className="mt-2 space-y-3">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            结果条数：<span className="text-cyan-300">{matrixRows.length}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            组合网格：<span className="text-cyan-300">{strategyMatrix?.grid_size ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            策略数：<span className="text-cyan-300">{strategyMatrix?.strategy_count ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            推荐（平衡）：
            <span className="text-emerald-300">{matrixBestBalanced?.strategy || "-"}</span>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">策略</th>
                <th className="py-1">K线</th>
                <th className="py-1">回测天数</th>
                <th className="py-1">成本(bps)</th>
                <th className="py-1">净收益%</th>
                <th className="py-1">回撤%</th>
                <th className="py-1">Sharpe</th>
                <th className="py-1">胜率%</th>
                <th className="py-1">样本</th>
                <th className="py-1">矩阵分</th>
              </tr>
            </thead>
            <tbody>
              {matrixRows.slice(0, 20).map((x, idx) => (
                <tr key={`${x.strategy || "s"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">
                    <div>{x.strategy_label || x.strategy || "-"}</div>
                    {x.strategy_params && Object.keys(x.strategy_params).length ? (
                      <div className="text-[11px] text-slate-400">{JSON.stringify(x.strategy_params)}</div>
                    ) : null}
                  </td>
                  <td className="py-1">{x.kline || "-"}</td>
                  <td className="py-1">{x.backtest_days ?? "-"}</td>
                  <td className="py-1">
                    {(x.commission_bps ?? "-")}/{(x.slippage_bps ?? "-")}
                  </td>
                  <td className="py-1 text-emerald-300">{x.avg_net_return_pct ?? "-"}</td>
                  <td className="py-1 text-amber-300">{x.avg_max_drawdown_pct ?? "-"}</td>
                  <td className="py-1">{x.avg_sharpe_ratio ?? "-"}</td>
                  <td className="py-1">{x.avg_win_rate_pct ?? "-"}</td>
                  <td className="py-1">
                    {x.symbols_used ?? "-"}/{x.symbols_total ?? "-"}
                  </td>
                  <td className="py-1 text-cyan-300">{x.matrix_score ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )}
  </details>

  <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3" open>
    <summary className="cursor-pointer text-sm font-medium text-slate-200">ML参数矩阵（可信参数筛选）</summary>
    {mlMatrixRunning && mlMatrixTaskId ? <div className="mt-2 text-xs text-amber-300">任务运行中：{mlMatrixTaskId}</div> : null}
    {!mlMatrixRows.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无ML矩阵结果，点击“ML参数矩阵筛选”开始。</div>
    ) : (
      <div className="mt-2 space-y-3">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            结果条数：<span className="text-cyan-300">{mlMatrixRows.length}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            组合网格：<span className="text-cyan-300">{mlMatrix?.grid_size ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            通过约束：<span className="text-cyan-300">{mlMatrix?.passed_constraints_count ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            推荐（平衡）：
            <span className="text-emerald-300">{mlMatrixBestBalanced?.params?.model_type || "-"}</span>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1120px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">模型</th>
                <th className="py-1">阈值</th>
                <th className="py-1">Horizon</th>
                <th className="py-1">TrainRatio</th>
                <th className="py-1">WF窗口</th>
                <th className="py-1">Acc</th>
                <th className="py-1">Precision</th>
                <th className="py-1">Recall</th>
                <th className="py-1">Coverage</th>
                <th className="py-1">OOS</th>
                <th className="py-1">通过</th>
                <th className="py-1">评分</th>
              </tr>
            </thead>
            <tbody>
              {mlMatrixRows.slice(0, 20).map((x, idx) => (
                <tr key={`${x?.params?.model_type || "mm"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{x?.params?.model_type || "-"}</td>
                  <td className="py-1">{x?.params?.ml_threshold ?? "-"}</td>
                  <td className="py-1">{x?.params?.ml_horizon_days ?? "-"}</td>
                  <td className="py-1">{x?.params?.ml_train_ratio ?? "-"}</td>
                  <td className="py-1">{x?.params?.ml_walk_forward_windows ?? "-"}</td>
                  <td className="py-1">{x?.metrics?.accuracy ?? "-"}</td>
                  <td className="py-1 text-cyan-300">{x?.metrics?.precision ?? "-"}</td>
                  <td className="py-1">{x?.metrics?.recall ?? "-"}</td>
                  <td className="py-1 text-emerald-300">{x?.metrics?.coverage ?? "-"}</td>
                  <td className="py-1">{x?.metrics?.oos_samples ?? "-"}</td>
                  <td className="py-1">{x?.pass_constraints ? "是" : "否"}</td>
                  <td className="py-1 text-sky-300">{x?.score ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )}
  </details>

  <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3" open>
    <summary className="cursor-pointer text-sm font-medium text-slate-200">ML诊断详情（Walk-forward）</summary>
    {!mlDiagModels.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无 ML 诊断详情，先执行一次 Research。</div>
    ) : (
      <div className="mt-2 space-y-2">
        <div className="text-xs text-slate-400">
          样本 {mlDiag?.dataset?.samples ?? "-"} · 使用标的 {mlDiag?.dataset?.symbols_used ?? "-"} /
          {mlDiag?.dataset?.symbols_requested ?? "-"} · 特征 {mlDiag?.settings?.feature_count ?? "-"} 个
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">模型</th>
                <th className="py-1">最新UpProb</th>
                <th className="py-1">Acc</th>
                <th className="py-1">Precision</th>
                <th className="py-1">Recall</th>
                <th className="py-1">Coverage</th>
                <th className="py-1">窗口数</th>
                <th className="py-1">OOS样本</th>
                <th className="py-1">WF覆盖条数</th>
              </tr>
            </thead>
            <tbody>
              {mlDiagModels.map((x, idx) => {
                const wf = x?.walk_forward || {};
                return (
                  <tr key={`${x.model_name || "ml"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                    <td className="py-1">{x.model_name || "-"}</td>
                    <td className="py-1 text-cyan-300">{x.latest_up_probability ?? "-"}</td>
                    <td className="py-1">{wf.accuracy ?? "-"}</td>
                    <td className="py-1">{wf.precision ?? "-"}</td>
                    <td className="py-1">{wf.recall ?? "-"}</td>
                    <td className="py-1">{wf.coverage ?? "-"}</td>
                    <td className="py-1">{wf.windows ?? "-"}</td>
                    <td className="py-1">{wf.oos_samples ?? "-"}</td>
                    <td className="py-1">{x.walk_forward_coverage ?? "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    )}
  </details>

  <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3" open>
    <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-200">
      <span>A/B 报告（Baseline vs WithFactor）</span>
      <span
        className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] ${
          Number(factorGating?.available_symbols || 0) > 0
            ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
            : "bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30"
        }`}
        title={
          Number(factorGating?.available_symbols || 0) > 0
            ? "已检测到可用外部因子样本"
            : "当前外部因子样本不可用，报告仍可用于结构对比"
        }
      >
        {Number(factorGating?.available_symbols || 0) > 0 ? "因子数据可用" : "因子数据不足"}
      </span>
    </summary>
    {!abReport ? (
      <div className="mt-2 text-xs text-slate-400">暂无 A/B 报告，先执行一次 Research。</div>
    ) : (
      <div className="mt-2 space-y-3">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            Top5重合：<span className="text-cyan-300">{abSummary?.overlap_count ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            平均最佳分Δ：<span className="text-emerald-300">{abSummary?.avg_best_score_delta ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            分配换手：<span className="text-amber-300">{abSummary?.allocation_turnover ?? "-"}</span>
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2 text-xs text-slate-300">
            生成时间：<span className="text-slate-200">{formatTime(abReport?.generated_at)}</span>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-2 text-xs text-slate-300 md:grid-cols-2">
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2">
            Baseline Top5：{(abSummary?.top5_baseline || []).join(", ") || "-"}
          </div>
          <div className="rounded border border-slate-700/70 bg-slate-950/40 p-2">
            WithFactor Top5：{(abSummary?.top5_with_factor || []).join(", ") || "-"}
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead className="bg-slate-900/60 text-left text-slate-300">
              <tr>
                <th className="px-3 py-2">标的</th>
                <th className="px-3 py-2">Score(B)</th>
                <th className="px-3 py-2">Score(F)</th>
                <th className="px-3 py-2">ΔScore</th>
                <th className="px-3 py-2">Multiplier</th>
                <th className="px-3 py-2">W(B)</th>
                <th className="px-3 py-2">W(F)</th>
                <th className="px-3 py-2">ΔW</th>
              </tr>
            </thead>
            <tbody>
              {abItems.slice(0, 10).map((x, idx) => (
                <tr key={`${x.symbol || "ab"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="px-3 py-2">{x.symbol || "-"}</td>
                  <td className="px-3 py-2">{x.score_baseline ?? "-"}</td>
                  <td className="px-3 py-2">{x.score_with_factor ?? "-"}</td>
                  <td className="px-3 py-2">{x.score_delta ?? "-"}</td>
                  <td className="px-3 py-2">{x.factor_multiplier ?? "-"}</td>
                  <td className="px-3 py-2">{x.weight_baseline ?? "-"}</td>
                  <td className="px-3 py-2">{x.weight_with_factor ?? "-"}</td>
                  <td className="px-3 py-2">{x.weight_delta ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )}
  </details>

  <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3" open>
    <summary className="cursor-pointer text-sm font-medium text-slate-200">分配计划表（P1）</summary>
    {!allocationRows.length ? (
      <div className="mt-2 text-xs text-slate-400">暂无分配计划，先执行一次 Research。</div>
    ) : (
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-slate-900/60 text-left text-slate-300">
            <tr>
              <th className="px-3 py-2">代码</th>
              <th className="px-3 py-2">原始权重</th>
              <th className="px-3 py-2">建议权重</th>
              <th className="px-3 py-2">Δ权重</th>
              <th className="px-3 py-2">强度分数</th>
              <th className="px-3 py-2">价格类型</th>
            </tr>
          </thead>
          <tbody>
            {allocationRows.map((x, idx) => (
              <tr key={`${x.symbol || "alloc"}-${idx}`} className="border-t border-slate-800/90">
                <td className="px-3 py-2 font-medium text-slate-100">{x.symbol || "-"}</td>
                <td className="px-3 py-2 text-slate-300">
                  {typeof x.weight_raw === "number" ? `${(x.weight_raw * 100).toFixed(2)}%` : "-"}
                </td>
                <td className="px-3 py-2 text-cyan-300">
                  {typeof x.weight === "number" ? `${(x.weight * 100).toFixed(2)}%` : "-"}
                </td>
                <td
                  className={`px-3 py-2 ${
                    typeof x.weight === "number" && typeof x.weight_raw === "number"
                      ? x.weight - x.weight_raw >= 0
                        ? "text-emerald-300"
                        : "text-amber-300"
                      : "text-slate-400"
                  }`}
                >
                  {typeof x.weight === "number" && typeof x.weight_raw === "number"
                    ? `${((x.weight - x.weight_raw) * 100).toFixed(2)}%`
                    : "-"}
                </td>
                <td className="px-3 py-2">{x.strength_score ?? "-"}</td>
                <td className="px-3 py-2 text-xs text-slate-300">{x.price_type || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )}
  </details>

  <details className="rounded-lg border border-slate-700/70 bg-slate-900/50 p-3" open>
    <summary className="cursor-pointer text-sm font-medium text-slate-200">ETF配对回测（只读快照）</summary>
    {!pairBacktest ? (
      <div className="mt-2 text-xs text-slate-400">暂无快照回测结果，请先执行一次 Research。</div>
    ) : pairBacktest?.error ? (
      <div className="mt-2 text-xs text-rose-300">回测快照错误：{String(pairBacktest.error)}</div>
    ) : (
      <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-5">
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">市场：{pairBacktest.market ?? "-"}</div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">K线：{pairBacktest.kline ?? "-"}</div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm text-emerald-300">
          总收益估算：{pairBacktest?.portfolio_estimate?.total_return_pct ?? "-"}%
        </div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm text-amber-300">
          平均回撤估算：{pairBacktest?.portfolio_estimate?.avg_selected_max_drawdown_pct ?? "-"}%
        </div>
        <div className="rounded-lg border border-slate-700/70 bg-slate-900/60 p-3 text-sm">
          入选配对数：{(pairBacktest?.selected_pairs || []).length}
        </div>
      </div>
    )}
    <div className="mt-3 rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
      <div className="text-xs text-slate-400">
        配对池配置：{pairPoolUsed.length} 组
      </div>
      {!pairPoolUsed.length ? (
        <div className="mt-2 text-xs text-slate-400">当前市场未配置 ETF 配对池。</div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[520px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">多头ETF</th>
                <th className="py-1">反向ETF</th>
              </tr>
            </thead>
            <tbody>
              {pairPoolUsed.map((row, idx) => (
                <tr key={`${row.long_symbol || "long"}-${row.short_symbol || "short"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{row.long_symbol || "-"}</td>
                  <td className="py-1">{row.short_symbol || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
    <div className="mt-3 rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
      <div className="text-xs text-slate-400">
        本次回测入选组合：{selectedPairRows.length} 组
      </div>
      {!selectedPairRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无入选组合（可能是配对池为空或全部评分未通过）。</div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[760px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">配对</th>
                <th className="py-1">入选标的</th>
                <th className="py-1">入选策略</th>
                <th className="py-1">综合分</th>
                <th className="py-1">收益%</th>
                <th className="py-1">回撤%</th>
              </tr>
            </thead>
            <tbody>
              {selectedPairRows.map((row: any, idx: number) => {
                const pair = row?.pair || {};
                const metrics = row?.selected_metrics || {};
                return (
                  <tr key={`${pair?.long || "long"}-${pair?.short || "short"}-${idx}`} className="border-t border-slate-800/90 text-slate-200">
                    <td className="py-1">{`${pair?.long || "-"} / ${pair?.short || "-"}`}</td>
                    <td className="py-1 text-cyan-300">{row?.selected_symbol || "-"}</td>
                    <td className="py-1">{metrics?.strategy_label || row?.selected_strategy || "-"}</td>
                    <td className="py-1">{row?.selected_score ?? "-"}</td>
                    <td className="py-1 text-emerald-300">{metrics?.total_return_pct ?? "-"}</td>
                    <td className="py-1 text-amber-300">{metrics?.max_drawdown_pct ?? "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
    <details className="mt-3 rounded-lg border border-slate-700/70 bg-slate-900/50 p-3">
      <summary className="cursor-pointer text-xs text-slate-300">
        组合交易明细（买入/卖出时间）：{pairTradeRows.length} 笔（默认折叠）
      </summary>
      <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
        <input
          className={INPUT_CLS}
          placeholder="按组合筛选（示例：SPY.US / SH.US）"
          value={pairTradeFilterPair}
          onChange={(e) => setPairTradeFilterPair(e.target.value)}
        />
        <input
          className={INPUT_CLS}
          placeholder="按标的筛选（示例：SPY.US）"
          value={pairTradeFilterSymbol}
          onChange={(e) => setPairTradeFilterSymbol(e.target.value)}
        />
      </div>
      {!filteredPairTradeRows.length ? (
        <div className="mt-2 text-xs text-slate-400">暂无交易明细（可能当前组合无成交或快照版本较旧）。</div>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[1080px] text-xs">
            <thead className="text-left text-slate-400">
              <tr>
                <th className="py-1">配对</th>
                <th className="py-1">入选标的</th>
                <th className="py-1">策略</th>
                <th className="py-1">买入时间</th>
                <th className="py-1">卖出时间</th>
                <th className="py-1">买入价</th>
                <th className="py-1">卖出价</th>
                <th className="py-1">数量</th>
                <th className="py-1">收益%</th>
                <th className="py-1">收益额</th>
                <th className="py-1">持有天数</th>
              </tr>
            </thead>
            <tbody>
              {filteredPairTradeRows.map((r: any) => (
                <tr key={r.id} className="border-t border-slate-800/90 text-slate-200">
                  <td className="py-1">{r.pair}</td>
                  <td className="py-1 text-cyan-300">{r.symbol}</td>
                  <td className="py-1">{r.strategy}</td>
                  <td className="py-1">{r.entry_date || "-"}</td>
                  <td className="py-1">{r.exit_date || "-"}</td>
                  <td className="py-1">{r.entry_price ?? "-"}</td>
                  <td className="py-1">{r.exit_price ?? "-"}</td>
                  <td className="py-1">{r.quantity ?? "-"}</td>
                  <td className={`py-1 ${Number(r.pnl_pct) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                    {r.pnl_pct ?? "-"}
                  </td>
                  <td className={`py-1 ${Number(r.pnl) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                    {r.pnl ?? "-"}
                  </td>
                  <td className="py-1">{r.hold_days ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  </details>
</div>
