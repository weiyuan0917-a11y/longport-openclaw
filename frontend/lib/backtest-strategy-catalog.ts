/**
 * 与 mcp_server/strategies.py 中 STRATEGY_METADATA 默认参数保持一致。
 * 当 GET /backtest/strategies 不可用（404 等）时，回测页用此数据渲染参数表单。
 */
export type StrategyCatalogItem = {
  name: string;
  label: string;
  description?: string;
  default_params: Record<string, number>;
};

export const FALLBACK_STRATEGY_CATALOG: StrategyCatalogItem[] = [
  {
    name: "ma_cross",
    label: "双均线交叉",
    description: "MA_fast 上穿 MA_slow 买入，下穿卖出",
    default_params: { fast: 5, slow: 20 },
  },
  {
    name: "rsi",
    label: "RSI 超买超卖",
    default_params: { period: 14, oversold: 30, overbought: 70 },
  },
  {
    name: "macd",
    label: "MACD 金叉死叉",
    default_params: { fast: 12, slow: 26, signal: 9 },
  },
  {
    name: "bollinger",
    label: "布林带突破",
    default_params: { period: 20, std_dev: 2.0 },
  },
  {
    name: "beiming",
    label: "北冥有鱼",
    default_params: {
      overlap: 5,
      oscillation_ratio: 0.05,
      breakout_body_min: 0.8,
      breakout_shadow_max: 0.1,
      stop_loss_ratio: 0.01,
      profit_loss_ratio: 9.0,
    },
  },
  {
    name: "donchian_breakout",
    label: "Donchian 海龟突破",
    default_params: { entry_period: 20, exit_period: 10 },
  },
  {
    name: "supertrend",
    label: "SuperTrend 趋势跟随",
    default_params: { period: 10, multiplier: 3.0 },
  },
  {
    name: "adx_ma_filter",
    label: "ADX过滤双均线",
    default_params: { fast: 10, slow: 30, adx_period: 14, adx_threshold: 20 },
  },
];
