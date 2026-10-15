import { z } from "zod";

// ---- Signal bot (candlestick patterns -> Telegram / Discord) ----

export const SignalPattern = z.object({
  id: z.string(),
  label: z.string(),
  direction: z.enum(["long", "short"]),
  bars: z.number().int(),
});
export type SignalPattern = z.infer<typeof SignalPattern>;

export const AnalysisChecks = z.object({
  trend: z.boolean().default(false),
  volume: z.boolean().default(false),
  rsi: z.boolean().default(false),
  macd: z.boolean().default(false),
  support: z.boolean().default(false),
});
export type AnalysisChecks = z.infer<typeof AnalysisChecks>;

export const SmcOverlayFlags = z.object({
  zones: z.boolean().default(false),
  structure: z.boolean().default(false),
  liquidity: z.boolean().default(false),
});
export type SmcOverlayFlags = z.infer<typeof SmcOverlayFlags>;

export const SignalAnalysis = z.object({
  enabled: z.boolean().default(false),
  block: z.boolean().default(false),
  min_score: z.number().int().min(1).max(5).default(3),
  show_details: z.boolean().default(true),
  checks: AnalysisChecks.default({
    trend: false,
    volume: false,
    rsi: false,
    macd: false,
    support: false,
  }),
  smc: SmcOverlayFlags.default({
    zones: false,
    structure: false,
    liquidity: false,
  }),
});
export type SignalAnalysis = z.infer<typeof SignalAnalysis>;

export const ConfluenceCheck = z.object({
  id: z.string(),
  label: z.string(),
  passed: z.boolean().nullable(),
  detail: z.string(),
});
export type ConfluenceCheck = z.infer<typeof ConfluenceCheck>;

export const Confluence = z.object({
  score: z.number().int(),
  total: z.number().int(),
  checks: z.array(ConfluenceCheck),
});
export type Confluence = z.infer<typeof Confluence>;

export const DISCORD_ROUTE_IDS = [
  "stock_1m",
  "stock_5m",
  "stock_15m",
  "stock_30m",
  "stock_1h",
  "stock_1d",
  "stock_1w",
  "crypto_1m",
  "crypto_5m",
  "crypto_15m",
  "crypto_30m",
  "crypto_1h",
  "crypto_1d",
  "crypto_1w",
  "stock_news",
  "crypto_news",
] as const;

export const DiscordRoute = z.enum(DISCORD_ROUTE_IDS);
export type DiscordRoute = z.infer<typeof DiscordRoute>;

export const WatchList = z
  .object({
    id: z.string(),
    name: z.string(),
    symbols: z.array(z.string()),
    telegram_timeframes: z.array(z.string()).optional(),
    discord_timeframes: z.array(z.string()).optional(),
    /** @deprecated legacy — both channels inherit on load */
    timeframes: z.array(z.string()).optional(),
    patterns: z.array(z.string()),
    discord_route: DiscordRoute.nullable().optional(),
    analysis: SignalAnalysis.optional(),
  })
  .transform((w) => {
    const legacy = w.timeframes;
    const telegram_timeframes =
      w.telegram_timeframes ?? legacy ?? ["1d"];
    const discord_timeframes =
      w.discord_timeframes ?? legacy ?? ["1d"];
    const { timeframes: _legacy, ...rest } = w;
    return {
      ...rest,
      telegram_timeframes,
      discord_timeframes,
      analysis: SignalAnalysis.parse(w.analysis ?? {}),
    };
  });
export type WatchList = z.infer<typeof WatchList>;

export const SignalsConfig = z.object({
  lists: z.array(WatchList),
  /** @deprecated ignored by the bot — scheduling is per bar close */
  poll_minutes: z.number().int().optional(),
});
export type SignalsConfig = z.infer<typeof SignalsConfig>;

export const DiscordConfigured = z.record(z.string(), z.boolean());
export type DiscordConfigured = z.infer<typeof DiscordConfigured>;

export const SignalsConfigResponse = z.object({
  config: SignalsConfig,
  patterns: z.array(SignalPattern),
  telegram_configured: z.boolean(),
  discord_configured: DiscordConfigured,
  discord_available_timeframes: z.array(z.string()),
});
export type SignalsConfigResponse = z.infer<typeof SignalsConfigResponse>;

export const SignalsStatus = z.object({
  running: z.boolean(),
  last_check: z.string().nullable(),
  heartbeat_age_s: z.number().nullable(),
});
export type SignalsStatus = z.infer<typeof SignalsStatus>;

export const SignalEntry = z.object({
  key: z.string(),
  ts: z.string(),
  symbol: z.string(),
  timeframe: z.string(),
  pattern_id: z.string(),
  label: z.string(),
  direction: z.enum(["long", "short"]),
  close: z.number(),
  entry: z.number(),
  stop: z.number().nullable(),
  target: z.number().nullable(),
  bar_ts: z.string(),
  list: z.string().optional(),
  confluence: Confluence.optional(),
  outcome: z.enum(["win", "loss", "expired"]).optional(),
  filtered: z.boolean().optional(),
});
export type SignalEntry = z.infer<typeof SignalEntry>;

export const DiscordSyncConflict = z.object({
  route: z.string(),
  channels: z.array(z.string()),
});
export type DiscordSyncConflict = z.infer<typeof DiscordSyncConflict>;

export const DiscordSyncRouteMeta = z.object({
  channel_name: z.string().optional(),
  channel_id: z.string().optional(),
});
export type DiscordSyncRouteMeta = z.infer<typeof DiscordSyncRouteMeta>;

export const DiscordSyncStatus = z.object({
  synced_at: z.string().nullable(),
  routes: z.record(z.string(), DiscordSyncRouteMeta),
  unmatched_channels: z.array(z.string()),
  conflicts: z.array(DiscordSyncConflict),
  skipped_no_webhook: z.array(z.string()).optional(),
});
export type DiscordSyncStatus = z.infer<typeof DiscordSyncStatus>;

export const DiscordSyncRunResponse = z.object({
  synced_at: z.string().nullable().optional(),
  routes: z.array(z.string()),
  unmatched_channels: z.array(z.string()),
  conflicts: z.array(DiscordSyncConflict),
  skipped_no_webhook: z.array(z.string()).optional(),
});
export type DiscordSyncRunResponse = z.infer<typeof DiscordSyncRunResponse>;

export const OutcomeBucket = z.object({
  total: z.number(),
  wins: z.number(),
  losses: z.number(),
  expired: z.number(),
  pending: z.number(),
});
export type OutcomeBucket = z.infer<typeof OutcomeBucket>;

export const SignalsStats = z.object({
  by_score: z.array(OutcomeBucket.extend({ score: z.number() })),
  sent: OutcomeBucket,
  filtered: OutcomeBucket,
});
export type SignalsStats = z.infer<typeof SignalsStats>;

export const SignalsHistoryResponse = z.object({ signals: z.array(SignalEntry) });
export type SignalsHistoryResponse = z.infer<typeof SignalsHistoryResponse>;

/** Discord watchlists only — weekly bars (`1w` → yfinance `1wk`). */
export const DISCORD_TIMEFRAMES = [
  "1m",
  "5m",
  "15m",
  "30m",
  "1h",
  "1d",
  "1w",
] as const;
export type DiscordTimeframe = (typeof DISCORD_TIMEFRAMES)[number];
