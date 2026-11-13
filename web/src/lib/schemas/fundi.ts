import { z } from "zod";

// ---- Fundamentals & news (/api/fundi*) ----

export const FundaField = z.object({
  v: z.union([z.number(), z.string()]),
  src: z.string(),
  ts: z.string(),
});
export type FundaField = z.infer<typeof FundaField>;

export const FundaQuarter = z.object({
  end: z.string(),
  revenue: z.number().nullable().optional(),
  net_income: z.number().nullable().optional(),
  gross_profit: z.number().nullable().optional(),
  eps: z.number().nullable().optional(),
  src: z.string().optional(),
});
export type FundaQuarter = z.infer<typeof FundaQuarter>;

export const FundaSnapshot = z.object({
  symbol: z.string(),
  asset: z.enum(["stock", "crypto"]),
  updated: z.string(),
  fields: z.record(z.string(), FundaField),
  quarters: z.array(FundaQuarter),
  sources_used: z.array(z.string()),
  errors: z.array(z.string()).optional(),
});
export type FundaSnapshot = z.infer<typeof FundaSnapshot>;

export const FundaWatchlist = z.object({
  id: z.string(),
  name: z.string(),
  stocks: z.array(z.string()),
  crypto: z.array(z.string()),
});
export type FundaWatchlist = z.infer<typeof FundaWatchlist>;

export const FundaQuota = z.object({
  used: z.number(),
  cap: z.number().nullable(),
  ok: z.boolean(),
});
export type FundaQuota = z.infer<typeof FundaQuota>;

export const FundiResponse = z.object({
  lists: z.array(FundaWatchlist),
  snapshots: z.record(z.string(), FundaSnapshot),
  bot: z.object({
    running: z.boolean(),
    ts: z.string().nullable(),
    heartbeat_age_s: z.number().nullable(),
    news_configured: z.object({ stocks: z.boolean(), crypto: z.boolean() }),
  }),
  quota: z.record(z.string(), FundaQuota),
  keys: z.record(z.string(), z.boolean()),
});
export type FundiResponse = z.infer<typeof FundiResponse>;

export const NewsItem = z.object({
  id: z.string(),
  symbol: z.string(),
  ts: z.string(),
  title: z.string(),
  summary: z.string().nullable(),
  url: z.string(),
  source: z.string().nullable(),
  sentiment: z.number().nullable(),
});
export type NewsItem = z.infer<typeof NewsItem>;

export const FundiNewsResponse = z.object({
  news: z.array(NewsItem),
  sent_count: z.number(),
});
export type FundiNewsResponse = z.infer<typeof FundiNewsResponse>;

export const FundiRefreshResponse = z.object({
  refreshed: z.array(z.string()),
  snapshots: z.record(z.string(), FundaSnapshot),
  quota: z.record(z.string(), FundaQuota),
});
export type FundiRefreshResponse = z.infer<typeof FundiRefreshResponse>;
