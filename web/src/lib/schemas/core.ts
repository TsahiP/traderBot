import { z } from "zod";

// ---- API response schemas (validated on every fetch) ----

export const LiveSnapshot = z.object({
  connected: z.boolean(),
  reason: z.string().optional(),
  fallback: z.string().optional(),
  symbol: z.string().optional(),
  close: z.number().optional(),
  sma_fast: z.number().optional(),
  sma_slow: z.number().optional(),
  signal: z.number().int().optional(),
  market_open: z.boolean().optional(),
  account: z
    .object({
      equity: z.number(),
      cash: z.number(),
      buying_power: z.number(),
    })
    .optional(),
  position: z
    .object({
      qty: z.number(),
      avg_entry: z.number(),
      current: z.number(),
      market_value: z.number(),
      unrealized_pl: z.number(),
      unrealized_pl_pct: z.number(),
    })
    .nullable()
    .optional(),
});
export type LiveSnapshot = z.infer<typeof LiveSnapshot>;

export const Stats = z.object({
  realized: z.object({
    trades: z.number(),
    total_pnl: z.number(),
    wins: z.number(),
    win_rate: z.number().optional(),
  }),
  backtest: z
    .object({
      start: z.string(),
      end: z.string(),
      final_equity: z.number(),
      total_return: z.number(),
      cagr: z.number(),
      max_drawdown: z.number(),
      buy_hold: z.number(),
      trades: z.number(),
      wins: z.number(),
      win_rate: z.number().optional(),
    })
    .nullable(),
});
export type Stats = z.infer<typeof Stats>;

export const Trade = z.object({
  entry_date: z.string(),
  entry_price: z.number(),
  exit_date: z.string(),
  exit_price: z.number(),
  qty: z.number().int(),
  pnl: z.number(),
  source: z.enum(["live", "backtest"]),
});
export type Trade = z.infer<typeof Trade>;

export const TradesResponse = z.object({ trades: z.array(Trade) });
export type TradesResponse = z.infer<typeof TradesResponse>;

export const EquityResponse = z.object({
  dates: z.array(z.string()),
  equity: z.array(z.number()),
});
export type EquityResponse = z.infer<typeof EquityResponse>;

export const RunMetrics = z.object({
  start: z.string(),
  end: z.string(),
  final_equity: z.number(),
  total_return: z.number(),
  cagr: z.number(),
  max_drawdown: z.number(),
  buy_hold: z.number(),
  trades: z.number().int(),
  wins: z.number().int(),
  win_rate: z.number().nullable(),
  costs_total: z.number(),
});
export type RunMetrics = z.infer<typeof RunMetrics>;

export const RunTrade = z.object({
  entry_date: z.string(),
  entry_price: z.number(),
  exit_date: z.string().nullable(),
  exit_price: z.number().nullable(),
  side: z.enum(["long", "short"]),
  exit_type: z.enum(["signal", "eod", "open"]),
  pnl: z.number(),
  costs: z.number(),
});
export type RunTrade = z.infer<typeof RunTrade>;

export const RunMarker = z.object({
  index: z.number().int(),
  date: z.string(),
  side: z.enum(["buy", "sell"]),
  eod: z.boolean().optional(),
  price: z.number(),
});
export type RunMarker = z.infer<typeof RunMarker>;

export const RunSeries = z.object({
  dates: z.array(z.string()),
  open: z.array(z.number()),
  high: z.array(z.number()),
  low: z.array(z.number()),
  close: z.array(z.number()),
  volume: z.array(z.number().int()),
  sma_fast: z.array(z.number().nullable()),
  sma_slow: z.array(z.number().nullable()),
  equity: z.array(z.number()),
});
export type RunSeries = z.infer<typeof RunSeries>;

export const BacktestRun = z.object({
  meta: z.object({
    symbol: z.string(),
    strategy: z.string(),
    strategy_label: z.string(),
    timeframe: z.string(),
    qty: z.number().int(),
    capital: z.number(),
    allow_short: z.boolean(),
    cost_per_share: z.number(),
    flat_eod: z.boolean(),
    params: z.record(z.string(), z.number()),
  }),
  metrics: RunMetrics,
  series: RunSeries,
  markers: z.array(RunMarker),
  trades: z.array(RunTrade),
});
export type BacktestRun = z.infer<typeof BacktestRun>;

export const StrategyParamSpec = z.object({
  key: z.string(),
  label: z.string(),
  min: z.number(),
  max: z.number(),
  step: z.number(),
  default: z.number(),
  int: z.boolean().optional(),
  unit: z.string().optional(),
});
export type StrategyParamSpec = z.infer<typeof StrategyParamSpec>;

export const StrategySpec = z.object({
  id: z.string(),
  label: z.string(),
  description: z.string(),
  timeframes: z.array(z.string()),
  flat_eod: z.boolean(),
  default_allow_short: z.boolean(),
  default_timeframe: z.string(),
  params: z.array(StrategyParamSpec),
});
export type StrategySpec = z.infer<typeof StrategySpec>;

export const StrategiesResponse = z.array(StrategySpec);
export type StrategiesResponse = z.infer<typeof StrategiesResponse>;

export const AnalysisResponse = z.object({
  model: z.string(),
  analysis: z.string(),
});
export type AnalysisResponse = z.infer<typeof AnalysisResponse>;

export const ApiError = z.object({ error: z.string() });
export type ApiError = z.infer<typeof ApiError>;
