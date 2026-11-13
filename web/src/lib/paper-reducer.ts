import { z } from "zod";

import {
  ApprovalMode,
  FunnelStage,
  PaperSetup,
  type PaperEvent,
  type PaperPosition,
  type PaperProposal,
  type PaperSnapshot,
} from "@/lib/schemas/paper";

/* Pure state for the Paper desk: the bot's /state snapshot plus the live
   events applied on top. Cheap events (quotes, setups, proposals, pause,
   config, equity, stream status) are applied here; events that change
   positions or orders ask the hook for a fresh /state (`needsRefresh`),
   because positions come from Alpaca and are not rebuilt from events. */

export const FEED_MAX = 200;

/** Shown in the panels, not in the live feed. */
const QUIET = new Set(["quote", "equity", "stream.status", "setup.state"]);

const REFRESH = new Set([
  "fill",
  "trade.closed",
  "position.opened",
  "position.adopted",
  "order.submitted",
  "order.canceled",
  "order.rejected",
  "order.updated",
  "command",
  "decision",
  "bot.start",
]);

export type PaperView = {
  snapshot: PaperSnapshot | null;
  /** newest first */
  feed: PaperEvent[];
  lastEventId: number;
};

export function emptyView(): PaperView {
  return { snapshot: null, feed: [], lastEventId: 0 };
}

export function fromSnapshot(view: PaperView, snapshot: PaperSnapshot): PaperView {
  return { ...view, snapshot, lastEventId: Math.max(view.lastEventId, snapshot.last_event_id) };
}

export function needsRefresh(event: PaperEvent): boolean {
  return REFRESH.has(event.type);
}

const round2 = (x: number) => Math.round(x * 100) / 100;

/** Live price -> P&L in dollars and in R (when the trade has a stop). */
export function repricePosition(pos: PaperPosition, price: number): PaperPosition {
  const sign = pos.side === "long" ? 1 : -1;
  const pnl = round2(sign * (price - pos.avg_entry) * pos.qty);
  let r: number | null = null;
  const stop = pos.trade?.stop;
  if (stop != null) {
    const risk = sign * (pos.avg_entry - stop);
    r = risk > 0 ? round2((sign * (price - pos.avg_entry)) / risk) : null;
  }
  return { ...pos, price, pnl, r };
}

const QuotePayload = z.object({ price: z.number() });
const ConfigPayload = z.object({
  config_hash: z.string(),
  approval_mode: ApprovalMode,
  llm_mode: z.enum(["off", "shadow", "veto"]),
});
const EquityPayload = z.object({ equity: z.number(), cash: z.number() });
const StreamPayload = z.object({ stream: z.string(), status: z.string() });
const ProposalPayload = z.object({
  id: z.number().int(),
  qty: z.number().nullish(),
  max_loss: z.number().nullish(),
  plan: z.record(z.string(), z.unknown()).nullish(),
  stages: z.array(FunnelStage).nullish(),
});
const IdPayload = z.object({ id: z.number().int() });

function applyToSnapshot(s: PaperSnapshot, event: PaperEvent): PaperSnapshot {
  const p = event.payload;
  switch (event.type) {
    case "quote": {
      const q = QuotePayload.safeParse(p);
      if (!q.success || !event.symbol) return s;
      return {
        ...s,
        positions: s.positions.map((pos) => (pos.symbol === event.symbol ? repricePosition(pos, q.data.price) : pos)),
      };
    }
    case "setup.state": {
      const row = PaperSetup.safeParse(p);
      if (!row.success) return s;
      const key = (x: { run_id: string; symbol: string; day: string }) => `${x.run_id}|${x.symbol}|${x.day}`;
      const others = s.setups.filter((x) => key(x) !== key(row.data));
      return { ...s, setups: [...others, row.data] };
    }
    case "proposal.created": {
      const pr = ProposalPayload.safeParse(p);
      if (!pr.success || s.proposals.some((x) => x.id === pr.data.id)) return s;
      const side = typeof pr.data.plan?.side === "string" ? pr.data.plan.side : null;
      const proposal: PaperProposal = {
        id: pr.data.id,
        run_id: event.run_id,
        symbol: event.symbol,
        side,
        qty: pr.data.qty ?? null,
        max_loss: pr.data.max_loss ?? null,
        plan: pr.data.plan ?? null,
        stages: pr.data.stages ?? null,
      };
      return { ...s, proposals: [...s.proposals, proposal] };
    }
    case "proposal.approved":
    case "proposal.rejected":
    case "proposal.expired": {
      const id = IdPayload.safeParse(p);
      if (!id.success) return s;
      return { ...s, proposals: s.proposals.filter((x) => x.id !== id.data.id) };
    }
    case "bot.paused":
      return { ...s, paused: true };
    case "bot.resumed":
      return { ...s, paused: false };
    case "bot.config": {
      const c = ConfigPayload.safeParse(p);
      return c.success ? { ...s, config: { ...s.config, ...c.data } } : s;
    }
    case "equity": {
      const e = EquityPayload.safeParse(p);
      if (!e.success || !s.account) return s;
      return { ...s, account: { ...s.account, equity: e.data.equity, cash: e.data.cash } };
    }
    case "stream.status": {
      const st = StreamPayload.safeParse(p);
      return st.success ? { ...s, streams: { ...s.streams, [st.data.stream]: st.data.status } } : s;
    }
    case "trade.closed":
      return { ...s, positions: s.positions.filter((pos) => pos.symbol !== event.symbol) };
    default:
      return s;
  }
}

export function applyEvent(view: PaperView, event: PaperEvent): PaperView {
  if (event.id != null && event.id <= view.lastEventId) {
    // already in the snapshot: only backfill the feed (the stream replays recent history on connect)
    if (QUIET.has(event.type) || view.feed.some((e) => e.id === event.id)) return view;
    const feed = [...view.feed, event].sort((a, b) => (b.id ?? 0) - (a.id ?? 0)).slice(0, FEED_MAX);
    return { ...view, feed };
  }
  const snapshot = view.snapshot ? applyToSnapshot(view.snapshot, event) : null;
  const feed = QUIET.has(event.type) ? view.feed : [event, ...view.feed].slice(0, FEED_MAX);
  const lastEventId = event.id != null ? Math.max(view.lastEventId, event.id) : view.lastEventId;
  return { snapshot, feed, lastEventId };
}
