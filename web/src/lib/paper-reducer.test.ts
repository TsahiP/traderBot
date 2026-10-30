import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FEED_MAX,
  applyEvent,
  emptyView,
  fromSnapshot,
  needsRefresh,
} from "@/lib/paper-reducer";
import type { PaperEvent, PaperSnapshot } from "@/lib/schemas/paper";

let nextId = 100;

function ev(type: string, payload: Record<string, unknown> = {}, extra: Partial<PaperEvent> = {}): PaperEvent {
  return { id: nextId++, ts: "2026-10-30T10:00:00-04:00", type, run_id: "swing_sma", symbol: "SPY", payload, ...extra };
}

function snapshot(): PaperSnapshot {
  return {
    ts: "2026-10-30T10:00:00-04:00",
    pid: 7,
    paused: false,
    market: "open",
    next_wake: null,
    streams: { trading: "up", quotes: "up" },
    config: { config_hash: "abc", approval_mode: "auto", llm_mode: "shadow", runs: [] },
    account: { equity: 100_000, cash: 90_000, buying_power: 180_000, shorting_enabled: true },
    positions: [
      {
        symbol: "SPY", side: "long", qty: 10, avg_entry: 100, current_price: 100, market_value: 1000,
        unrealized_pl: 0, price: 100, pnl: 0, r: 0,
        trade: { id: 1, run_id: "swing_sma", stop: 96, target: 108, entry_ts: "t", adopted: false,
                 flat_by: null, mae_r: 0, mfe_r: 0 },
      },
      {
        symbol: "NVDA", side: "short", qty: 5, avg_entry: 180, current_price: 180, market_value: -900,
        unrealized_pl: 0, price: 180, pnl: 0, r: null, trade: null,
      },
    ],
    proposals: [],
    setups: [{ run_id: "swing_sma", symbol: "SPY", day: "2026-10-30", state: "WAITING", sentence: "waiting" }],
    counters: { entries_today: 0, max_trades_per_day: 6, daily_loss_used: 0, max_daily_loss_usd: 500,
                open_risk_usd: 40, max_open_risk_usd: 600 },
    last_event_id: 99,
  };
}

const view = () => fromSnapshot(emptyView(), snapshot());

test("a quote moves price, P&L and R of the position, both sides", () => {
  let v = applyEvent(view(), ev("quote", { price: 102 }, { id: null }));
  const spy = v.snapshot!.positions.find((p) => p.symbol === "SPY")!;
  assert.equal(spy.price, 102);
  assert.equal(spy.pnl, 20);
  assert.equal(spy.r, 0.5);
  v = applyEvent(v, ev("quote", { price: 178 }, { id: null, symbol: "NVDA" }));
  const nvda = v.snapshot!.positions.find((p) => p.symbol === "NVDA")!;
  assert.equal(nvda.pnl, 10);
  assert.equal(nvda.r, null);
  assert.equal(v.feed.length, 0, "quotes stay out of the feed");
});

test("replayed events are applied once", () => {
  const e = ev("bot.paused");
  const once = applyEvent(view(), e);
  const twice = applyEvent(once, e);
  assert.equal(twice, once);
  assert.equal(twice.snapshot!.paused, true);
  assert.equal(twice.lastEventId, e.id);
});

test("backlog older than the snapshot fills the feed without touching the state", () => {
  let v = applyEvent(view(), ev("error", { message: "new" }, { id: 120 }));
  v = applyEvent(v, ev("bot.paused", {}, { id: 50 }));
  v = applyEvent(v, ev("fill", { qty: 1 }, { id: 60 }));
  v = applyEvent(v, ev("setup.state", {}, { id: 61 }));
  assert.equal(v.snapshot!.paused, false, "the snapshot already includes event 50");
  assert.deepEqual(v.feed.map((e) => e.id), [120, 60, 50]);
  assert.equal(applyEvent(v, ev("fill", {}, { id: 60 })), v);
  assert.equal(v.lastEventId, 120);
});

test("setups are upserted by run, symbol and day", () => {
  const row = { run_id: "swing_sma", symbol: "SPY", day: "2026-10-30", state: "TRIGGERED", sentence: "queued" };
  let v = applyEvent(view(), ev("setup.state", row));
  assert.equal(v.snapshot!.setups.length, 1);
  assert.equal(v.snapshot!.setups[0].state, "TRIGGERED");
  v = applyEvent(v, ev("setup.state", { ...row, symbol: "QQQ", state: "WAITING" }));
  assert.equal(v.snapshot!.setups.length, 2);
  assert.equal(applyEvent(v, ev("setup.state", { junk: true })).snapshot!.setups.length, 2);
});

test("proposals appear and leave on approve, reject or expire", () => {
  const created = ev("proposal.created", {
    id: 12, ticket: "proposed: long 50 SPY", qty: 50, max_loss: 200,
    plan: { side: "long", stop: 96, target: 108 }, stages: [{ stage: "risk", status: "pass", reason: "ok", ms: 0.1 }],
    expires_at: "2026-10-30T15:30:00-04:00",
  });
  let v = applyEvent(view(), created);
  assert.deepEqual(v.snapshot!.proposals.map((p) => [p.id, p.symbol, p.side, p.qty]), [[12, "SPY", "long", 50]]);
  assert.equal(applyEvent(v, created).snapshot!.proposals.length, 1);
  for (const kind of ["proposal.approved", "proposal.rejected", "proposal.expired"]) {
    assert.equal(applyEvent(v, ev(kind, { id: 12 })).snapshot!.proposals.length, 0, kind);
  }
  v = applyEvent(v, ev("proposal.expired", { id: 99 }));
  assert.equal(v.snapshot!.proposals.length, 1);
});

test("config, equity and stream status update in place", () => {
  let v = applyEvent(view(), ev("bot.config", { config_hash: "def", approval_mode: "manual", llm_mode: "off" }));
  assert.equal(v.snapshot!.config.approval_mode, "manual");
  assert.equal(v.snapshot!.config.llm_mode, "off");
  assert.equal(v.snapshot!.config.config_hash, "def");
  v = applyEvent(v, ev("equity", { equity: 100_250, cash: 89_000, open_pnl: 12 }, { id: null }));
  assert.equal(v.snapshot!.account!.equity, 100_250);
  v = applyEvent(v, ev("stream.status", { stream: "quotes", status: "down" }, { id: null }));
  assert.equal(v.snapshot!.streams.quotes, "down");
});

test("a closed trade drops its position and asks for a refresh", () => {
  const closed = ev("trade.closed", { trade_id: 1, exit_reason: "stop", pnl: -40, r: -1 });
  const v = applyEvent(view(), closed);
  assert.deepEqual(v.snapshot!.positions.map((p) => p.symbol), ["NVDA"]);
  assert.equal(needsRefresh(closed), true);
  assert.equal(v.feed[0].type, "trade.closed");
});

test("which events need a fresh /state", () => {
  for (const t of ["fill", "position.opened", "position.adopted", "order.submitted", "order.canceled", "command",
                   "decision", "bot.start"]) {
    assert.equal(needsRefresh(ev(t)), true, t);
  }
  for (const t of ["quote", "setup.state", "proposal.created", "bot.paused", "equity", "error"]) {
    assert.equal(needsRefresh(ev(t)), false, t);
  }
});

test("the feed is newest first and capped", () => {
  let v = view();
  for (let i = 0; i < FEED_MAX + 20; i++) v = applyEvent(v, ev("error", { message: `e${i}` }));
  assert.equal(v.feed.length, FEED_MAX);
  assert.equal(v.feed[0].payload.message, `e${FEED_MAX + 19}`);
});

test("events before any snapshot still reach the feed", () => {
  const v = applyEvent(emptyView(), ev("error", { message: "bot starting" }));
  assert.equal(v.snapshot, null);
  assert.equal(v.feed.length, 1);
});

test("a new snapshot keeps the feed and never moves lastEventId back", () => {
  let v = applyEvent(view(), ev("error", { message: "x" }));
  const high = v.lastEventId;
  v = fromSnapshot(v, { ...snapshot(), last_event_id: 5 });
  assert.equal(v.feed.length, 1);
  assert.equal(v.lastEventId, high);
});
