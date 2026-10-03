import { z } from "zod";

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
