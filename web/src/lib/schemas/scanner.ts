import { z } from "zod";

// ---- Market scanner (/api/scanner*) ----

export const ScanHit = z.object({
  symbol: z.string(),
  name: z.string().nullable().optional(),
  setups: z.array(z.string()),
  close: z.number(),
  change_pct: z.number().optional(),
  rsi: z.number().optional(),
  volume: z.number(),
  dollar_volume: z.number(),
  at: z.string().optional(),
  range_high: z.number().optional(),
  range_low: z.number().optional(),
});
export type ScanHit = z.infer<typeof ScanHit>;

export const ScanResults = z.object({
  ts: z.string(),
  mode: z.enum(["daily", "orb"]),
  scope: z.string(),
  requested: z.number().int(),
  with_data: z.number().int(),
  seconds: z.number(),
  filters: z.object({ min_price: z.number(), min_volume: z.number() }),
  hits: z.array(ScanHit),
  note: z.string().optional(),
});
export type ScanResults = z.infer<typeof ScanResults>;

export const ScannerResponse = z.object({
  results: ScanResults.nullable(),
  running: z.boolean(),
  started: z.string().nullable(),
  error: z.string().nullable(),
});
export type ScannerResponse = z.infer<typeof ScannerResponse>;
