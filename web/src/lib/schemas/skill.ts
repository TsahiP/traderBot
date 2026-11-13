import { z } from "zod";

export const SkillRow = z.object({
  symbol: z.string(),
  status: z.string(),
  sentence: z.string(),
  setups: z.array(z.string()),
  range_high: z.number().nullable(),
  range_low: z.number().nullable(),
  last_close: z.number().nullable(),
  entry: z.number().nullable(),
  stop: z.number().nullable(),
  target: z.number().nullable(),
  qty: z.number().nullable(),
  max_loss: z.number().nullable(),
  verdict: z.string().nullable(),
  verdict_reason: z.string().nullable(),
});
export type SkillRow = z.infer<typeof SkillRow>;

export const SkillBoard = z.object({
  ts: z.string(),
  rule: z.string(),
  scan: z.enum(["daily", "orb"]),
  note: z.string().nullable(),
  source: z.string(),
  symbols: z.number().int(),
  rows: z.array(SkillRow),
});
export type SkillBoard = z.infer<typeof SkillBoard>;

export const SkillResponse = z.object({
  running: z.boolean(),
  error: z.string().nullable(),
  account_error: z.string().nullable(),
  board: SkillBoard.nullable(),
});
export type SkillResponse = z.infer<typeof SkillResponse>;
