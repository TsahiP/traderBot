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

export const ApiError = z.object({ error: z.string() });
export type ApiError = z.infer<typeof ApiError>;

// ---- Backtest lab form schema ----

export const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "1d"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

const dateStr = z
  .union([z.literal(""), z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Use YYYY-MM-DD")])
  .optional();

const commonFields = {
  symbol: z
    .string()
    .trim()
    .min(1, "Ticker is required")
    .max(12, "Tickers are max 12 characters")
    .regex(/^[A-Za-z0-9.\-]+$/, "Letters, digits, dots and dashes only")
    .transform((v) => v.toUpperCase()),
  timeframe: z.enum(TIMEFRAMES),
  start: dateStr,
  end: dateStr,
  qty: z.coerce.number().int("Whole number").min(1, "Min 1").max(100000, "Max 100000"),
  capital: z.coerce.number().min(1, "Must be positive").max(1e12, "Too large"),
  allow_short: z.boolean(),
  cost_per_share: z.coerce.number().min(0, "Min $0").max(1, "Max $1 per share"),
} as const;

const smaVariant = z.object({
  ...commonFields,
  strategy: z.literal("sma_crossover"),
  fast: z.coerce.number().int("Whole number").min(1, "Min 1").max(500, "Max 500"),
  slow: z.coerce.number().int("Whole number").min(2, "Min 2").max(500, "Max 500"),
});

const vwapVariant = z.object({
  ...commonFields,
  strategy: z.literal("vwap_reversion"),
  deviation_pct: z.coerce.number().min(0.1, "Min 0.1").max(10, "Max 10"),
  exit_pct: z.coerce.number().min(0.05, "Min 0.05").max(5, "Max 5"),
});

const orbVariant = z.object({
  ...commonFields,
  strategy: z.literal("opening_range_breakout"),
  range_minutes: z.coerce.number().int("Whole number").min(5, "Min 5").max(120, "Max 120"),
  tp_mult: z.coerce.number().min(0.1, "Min 0.1").max(5, "Max 5"),
  sl_mult: z.coerce.number().min(0.1, "Min 0.1").max(5, "Max 5"),
  max_range_pct: z.coerce.number().min(0.1, "Min 0.1").max(3, "Max 3"),
});

const rsiVariant = z.object({
  ...commonFields,
  strategy: z.literal("rsi_mean_reversion"),
  rsi_period: z.coerce.number().int("Whole number").min(2, "Min 2").max(100, "Max 100"),
  oversold: z.coerce.number().min(1, "Min 1").max(99, "Max 99"),
  overbought: z.coerce.number().min(1, "Min 1").max(99, "Max 99"),
  exit_level: z.coerce.number().min(1, "Min 1").max(99, "Max 99"),
});

export const LabSchema = z
  .discriminatedUnion("strategy", [smaVariant, vwapVariant, orbVariant, rsiVariant])
  .superRefine((v, ctx) => {
    if (v.strategy === "sma_crossover" && v.fast >= v.slow) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Fast SMA must be below slow SMA",
        path: ["slow"],
      });
    }
    if (v.strategy === "vwap_reversion" && v.exit_pct >= v.deviation_pct) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Exit level must be below the entry deviation",
        path: ["exit_pct"],
      });
    }
    if (v.strategy === "rsi_mean_reversion") {
      if (v.oversold >= v.exit_level || v.overbought <= v.exit_level) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Need oversold < exit level < overbought",
          path: ["exit_level"],
        });
      }
    }
    if (v.start && v.end && v.start > v.end) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "From must be on or before To",
        path: ["start"],
      });
    }
  });

export type LabValues = z.infer<typeof LabSchema>;

// Defaults mirrored from strategies.py so the form is usable before
// /api/strategies resolves.
export const STRATEGY_DEFAULTS: Record<
  string,
  { timeframe: Timeframe; allow_short: boolean; params: Record<string, number> }
> = {
  sma_crossover: {
    timeframe: "1d",
    allow_short: false,
    params: { fast: 10, slow: 50 },
  },
  vwap_reversion: {
    timeframe: "5m",
    allow_short: true,
    params: { deviation_pct: 0.8, exit_pct: 0.2 },
  },
  opening_range_breakout: {
    timeframe: "5m",
    allow_short: true,
    params: { range_minutes: 15, tp_mult: 0.5, sl_mult: 1.0, max_range_pct: 0.55 },
  },
  rsi_mean_reversion: {
    timeframe: "15m",
    allow_short: true,
    params: { rsi_period: 14, oversold: 30, overbought: 70, exit_level: 50 },
  },
};