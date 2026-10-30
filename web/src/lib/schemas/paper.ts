import { z } from "zod";

/* Paper desk: paperbot's /state snapshot, its SSE events, and dashboard.py's
   read-only /api/paper/state fallback. Payloads are parsed per event type in
   lib/paper-reducer.ts; unknown fields pass through untouched. */

export const SetupStateName = z.enum([
  "IDLE",
  "ARMED",
  "WAITING",
  "TRIGGERED",
  "PROPOSED",
  "ENTERED",
  "MANAGING",
  "EXITED",
  "EXPIRED",
  "BLOCKED",
]);
export type SetupStateName = z.infer<typeof SetupStateName>;

export const PaperSetup = z.object({
  run_id: z.string(),
  symbol: z.string(),
  day: z.string(),
  state: SetupStateName,
  side: z.string().nullish(),
  level: z.number().nullish(),
  distance_pct: z.number().nullish(),
  sentence: z.string().nullish(),
  detail: z.record(z.string(), z.unknown()).nullish(),
  updated_at: z.string().nullish(),
});
export type PaperSetup = z.infer<typeof PaperSetup>;

export const FunnelStage = z.object({
  stage: z.string(),
  status: z.enum(["pass", "fail", "skip", "warn"]),
  value: z.unknown().optional(),
  reason: z.string().nullish(),
  ms: z.number().nullish(),
});
export type FunnelStage = z.infer<typeof FunnelStage>;

export const PaperProposal = z.object({
  id: z.number().int(),
  run_id: z.string().nullish(),
  symbol: z.string().nullish(),
  side: z.string().nullish(),
  qty: z.number().nullish(),
  max_loss: z.number().nullish(),
  plan: z.record(z.string(), z.unknown()).nullish(),
  stages: z.array(FunnelStage).nullish(),
});
export type PaperProposal = z.infer<typeof PaperProposal>;

export const PositionTrade = z.object({
  id: z.number().int(),
  run_id: z.string(),
  stop: z.number().nullable(),
  target: z.number().nullable(),
  entry_ts: z.string().nullable(),
  adopted: z.boolean().nullable(),
  flat_by: z.string().nullable(),
  mae_r: z.number().nullable(),
  mfe_r: z.number().nullable(),
});
export type PositionTrade = z.infer<typeof PositionTrade>;

export const PaperPosition = z.object({
  symbol: z.string(),
  side: z.enum(["long", "short"]),
  qty: z.number(),
  avg_entry: z.number(),
  current_price: z.number().nullish(),
  market_value: z.number().nullish(),
  unrealized_pl: z.number().nullish(),
  price: z.number(),
  pnl: z.number(),
  r: z.number().nullable(),
  trade: PositionTrade.nullable(),
});
export type PaperPosition = z.infer<typeof PaperPosition>;

export const PaperAccount = z.object({
  equity: z.number(),
  cash: z.number(),
  buying_power: z.number(),
  shorting_enabled: z.boolean().nullish(),
});
export type PaperAccount = z.infer<typeof PaperAccount>;

export const PaperCounters = z.object({
  entries_today: z.number(),
  max_trades_per_day: z.number().nullable(),
  daily_loss_used: z.number(),
  max_daily_loss_usd: z.number().nullable(),
  open_risk_usd: z.number(),
  max_open_risk_usd: z.number().nullable(),
});
export type PaperCounters = z.infer<typeof PaperCounters>;

export const ApprovalMode = z.enum(["auto", "manual"]);
export type ApprovalMode = z.infer<typeof ApprovalMode>;

export const PaperConfigSummary = z.object({
  config_hash: z.string(),
  approval_mode: ApprovalMode,
  llm_mode: z.enum(["off", "shadow", "veto"]),
  runs: z.array(z.object({ id: z.string(), style: z.string(), enabled: z.boolean() })),
});
export type PaperConfigSummary = z.infer<typeof PaperConfigSummary>;

/** paperbot GET /state once the loop has ticked. */
export const PaperSnapshot = z.object({
  ts: z.string(),
  pid: z.number().int(),
  paused: z.boolean(),
  market: z.string(),
  next_wake: z.string().nullable(),
  streams: z.record(z.string(), z.string()),
  config: PaperConfigSummary,
  account: PaperAccount.nullable(),
  positions: z.array(PaperPosition),
  proposals: z.array(PaperProposal),
  setups: z.array(PaperSetup),
  counters: PaperCounters,
  last_event_id: z.number().int(),
});
export type PaperSnapshot = z.infer<typeof PaperSnapshot>;

/** paperbot GET /state: the snapshot, or {starting: true} before the first tick. */
export const PaperStateResponse = z.union([PaperSnapshot, z.object({ starting: z.literal(true) })]);
export type PaperStateResponse = z.infer<typeof PaperStateResponse>;

export const PaperEvent = z.object({
  id: z.number().int().nullable(),
  ts: z.string(),
  type: z.string(),
  run_id: z.string().nullable(),
  symbol: z.string().nullable(),
  payload: z.record(z.string(), z.unknown()),
});
export type PaperEvent = z.infer<typeof PaperEvent>;

export const PaperCommandKind = z.enum([
  "pause",
  "resume",
  "flatten_all",
  "close_position",
  "set_approval",
  "approve",
  "reject",
]);
export type PaperCommandKind = z.infer<typeof PaperCommandKind>;

export const PaperCommandResponse = z.object({
  command_id: z.number().int(),
  status: z.string(),
});
export type PaperCommandResponse = z.infer<typeof PaperCommandResponse>;

/** dashboard.py GET /api/paper/state: history from the journal while the bot is down. */
export const PaperHistoryState = z.object({
  online: z.boolean(),
  empty: z.boolean(),
  heartbeat: z
    .object({
      ts: z.string().nullable(),
      pid: z.number().nullable(),
      paused: z.boolean().nullable(),
      market: z.string().nullable(),
      next_wake: z.string().nullable(),
      streams: z.record(z.string(), z.string()).nullable(),
      config_hash: z.string().nullable(),
    })
    .nullish(),
  open_trades: z.array(z.record(z.string(), z.unknown())).optional(),
  proposals: z.array(PaperProposal).optional(),
  setups: z.array(PaperSetup).optional(),
  counters: z.object({ entries_today: z.number(), daily_loss_used: z.number() }).optional(),
  last_event_id: z.number().int().optional(),
});
export type PaperHistoryState = z.infer<typeof PaperHistoryState>;
