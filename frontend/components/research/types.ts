export type ResearchStatus = {
  has_snapshot?: boolean;
  generated_at?: string | null;
  market?: string | null;
  kline?: string | null;
  top_n?: number | null;
  version?: string | null;
  task_queue?: {
    queued?: number;
    running?: number;
    active?: number;
    max_pending?: number;
    queued_by_type?: Record<string, number>;
    running_by_type?: Record<string, number>;
    active_tasks?: Array<{
      task_id?: string;
      task_type?: "research" | "strategy_matrix" | "ml_matrix" | string;
      status?: string;
      created_at?: string | null;
      started_at?: string | null;
      progress_pct?: number;
      progress_stage?: string;
      progress_text?: string;
      progress_updated_at?: string | null;
      queue_position?: number;
      queue_ahead?: number;
    }>;
  };
};

export type ResearchSnapshot = {
  has_snapshot?: boolean;
  snapshot?: {
    version?: string;
    generated_at?: string;
    market?: string;
    kline?: string;
    top_n?: number;
    pair_pool_used?: Array<{
      long_symbol?: string;
      short_symbol?: string;
    }>;
    pair_pool_size?: number;
    data_providers?: {
      primary?: string;
      openbb_enabled?: boolean;
      openbb_connected?: boolean;
      openbb_base_url?: string;
    };
    external_research?: {
      market_regime?: {
        available?: boolean;
        regime?: string;
        confidence?: number;
        as_of?: string;
        symbol?: string;
        features?: {
          ret_20?: number;
          vol_z?: number;
        };
        note?: string;
        reason?: string;
      };
      symbol_factors?: Array<{
        symbol?: string;
        source?: string;
        available?: boolean;
        volatility_30d?: number | null;
        sentiment_score?: number | null;
      }>;
    };
    strong_stocks?: unknown[];
    allocation_plan?: Array<{
      symbol?: string;
      weight_raw?: number;
      weight?: number;
      strength_score?: number;
      price_type?: string;
    }>;
    strategy_rankings?: Array<{
      symbol?: string;
      best_strategy?: {
        strategy?: string;
        strategy_label?: string;
        composite_score?: number;
        composite_score_raw?: number;
        regime_multiplier?: number;
        net_return_pct?: number;
        sharpe_ratio?: number;
        max_drawdown_pct?: number;
      };
    }>;
    regime_gating?: {
      applied?: boolean;
      regime_name?: string;
      regime_confidence?: number;
      max_single_ratio?: number;
      target_gross_exposure?: number;
      effective_exposure?: number;
      formula?: string;
    };
    factor_gating?: {
      applied?: boolean;
      available_symbols?: number;
      total_symbols?: number;
      formula?: string;
    };
    factor_ab_report?: {
      generated_at?: string;
      summary?: {
        top5_baseline?: string[];
        top5_with_factor?: string[];
        overlap_count?: number;
        overlap_symbols?: string[];
        entered_symbols?: string[];
        exited_symbols?: string[];
        avg_best_score_baseline?: number;
        avg_best_score_with_factor?: number;
        avg_best_score_delta?: number;
        allocation_turnover?: number;
      };
      items?: Array<{
        symbol?: string;
        score_baseline?: number | null;
        score_with_factor?: number | null;
        score_delta?: number | null;
        factor_multiplier?: number;
        weight_baseline?: number;
        weight_with_factor?: number;
        weight_delta?: number;
      }>;
    };
    ml_diagnostics?: {
      enabled?: boolean;
      reason?: string;
      settings?: {
        requested_model_type?: string;
        horizon_days?: number;
        train_ratio?: number;
        walk_forward_windows?: number;
        transaction_cost_bps?: number;
        feature_count?: number;
      };
      dataset?: {
        symbols_requested?: number;
        symbols_used?: number;
        bars_total?: number;
        samples?: number;
      };
      label_distribution?: {
        positive?: number;
        negative?: number;
        positive_ratio?: number;
      };
      net_future_ret_summary?: {
        mean?: number;
        p10?: number;
        p25?: number;
        p50?: number;
        p75?: number;
        p90?: number;
      };
      models?: Array<{
        model_name?: string;
        latest_up_probability?: number | null;
        metric_score?: number;
        walk_forward_coverage?: number;
        walk_forward?: {
          accuracy?: number;
          precision?: number;
          recall?: number;
          coverage?: number;
          windows?: number;
          oos_samples?: number;
        };
      }>;
    };
    pair_backtest?: Record<string, any>;
  };
};

export type ModelCompareResult = {
  count?: number;
  items?: Array<{
    model_name?: string;
    runs?: number;
    avg_score?: number;
    best_score?: number;
    avg_accuracy?: number;
  }>;
};

export type StrategyMatrixItem = {
  strategy?: string;
  strategy_label?: string;
  strategy_params?: Record<string, unknown>;
  top_symbols?: Array<{
    symbol?: string;
    net_return_pct?: number;
    max_drawdown_pct?: number;
    sharpe_ratio?: number;
    win_rate_pct?: number;
    trades?: number;
  }>;
  kline?: string;
  backtest_days?: number;
  commission_bps?: number;
  slippage_bps?: number;
  symbols_used?: number;
  symbols_total?: number;
  avg_net_return_pct?: number;
  avg_max_drawdown_pct?: number;
  avg_sharpe_ratio?: number;
  avg_win_rate_pct?: number;
  avg_trades?: number;
  matrix_score?: number;
};

export type StrategyMatrixPayload = {
  generated_at?: string;
  trace_id?: string;
  market?: string;
  ok?: boolean;
  grid_size?: number;
  strategy_count?: number;
  candidate_count?: number;
  symbols?: string[];
  best_balanced?: StrategyMatrixItem | null;
  best_aggressive?: StrategyMatrixItem | null;
  best_defensive?: StrategyMatrixItem | null;
  items?: StrategyMatrixItem[];
};

export type StrategyMatrixResult = {
  has_result?: boolean;
  result?: StrategyMatrixPayload;
};

export type MlMatrixItem = {
  params?: {
    model_type?: "logreg" | "random_forest" | "gbdt";
    ml_threshold?: number;
    ml_horizon_days?: number;
    ml_train_ratio?: number;
    ml_walk_forward_windows?: number;
    transaction_cost_bps?: number;
  };
  metrics?: {
    accuracy?: number;
    precision?: number;
    recall?: number;
    coverage?: number;
    oos_samples?: number;
    windows?: number;
  };
  dataset?: {
    symbols_used?: number;
    symbols_total?: number;
    samples?: number;
  };
  stability?: {
    coverage_std?: number;
    accuracy_std?: number;
    precision_std?: number;
  };
  score?: number;
  pass_constraints?: boolean;
  failed_reasons?: string[];
};

export type MlMatrixPayload = {
  generated_at?: string;
  trace_id?: string;
  market?: string;
  kline?: string;
  ok?: boolean;
  grid_size?: number;
  evaluated_count?: number;
  passed_constraints_count?: number;
  top_n?: number;
  signal_bars_days?: number;
  signal_bars_days_requested?: number;
  /** 后端因特征行数下限自动加长 K 线窗口时的说明 */
  signal_bars_days_note?: string;
  /** 各标的 raw bar 数与探测得到的特征行数（日K=交易日根数） */
  bar_fetch_preflight?: Array<{
    symbol?: string;
    raw_bars?: number;
    feature_rows?: number | null;
    meets_matrix_min?: boolean;
    error?: string;
    feature_error?: string | null;
  }>;
  best_balanced?: MlMatrixItem | null;
  best_high_precision?: MlMatrixItem | null;
  best_high_coverage?: MlMatrixItem | null;
  items?: MlMatrixItem[];
};

export type MlMatrixResult = {
  has_result?: boolean;
  result?: MlMatrixPayload;
};

export type FactorABMarkdownResult = {
  has_report?: boolean;
  generated_at?: string | null;
  markdown?: string;
};
