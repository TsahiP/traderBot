import assert from "node:assert/strict";
import { test } from "node:test";

import { bracketProgress, countdown, describeEvent, signedR, signedUsd } from "@/lib/paper-format";
import type { PaperEvent, PaperPosition } from "@/lib/schemas/paper";

const ev = (type: string, payload: Record<string, unknown>, symbol = "SPY"): PaperEvent => ({
  id: 1, ts: "2026-10-30T10:15:02-04:00", type, run_id: "swing_sma", symbol, payload,
});

test("entry fills and closed trades read like a trader would say them", () => {
  assert.deepEqual(describeEvent(ev("fill", { kind: "entry", side: "long", qty: 50, entry_price: 120.1 })),
    { text: "Entered long 50 SPY @ 120.10", tone: "info" });
  const loss = describeEvent(ev("trade.closed", { side: "long", qty: 50, exit_reason: "stop", exit_price: 116, pnl: -205, r: -1.025 }));
  assert.equal(loss.text, "Closed long 50 SPY by stop @ 116.00: -$205.00 (-1.02R)");
  assert.equal(loss.tone, "down");
  const win = describeEvent(ev("trade.closed", { side: "short", qty: 5, exit_reason: "target", exit_price: 175.3, pnl: 35, r: null }, "NVDA"));
  assert.equal(win.text, "Closed short 5 NVDA by target @ 175.30: +$35.00");
  assert.equal(win.tone, "up");
});

test("commands, decisions and errors", () => {
  assert.deepEqual(describeEvent(ev("command", { kind: "close_position", status: "error", result: { error: "no open position in TSLA" } })),
    { text: "Command close_position: error - no open position in TSLA", tone: "down" });
  assert.equal(describeEvent(ev("decision", { outcome: "blocked", summary: "blocked at risk: global trades" })).tone, "warn");
  assert.equal(describeEvent(ev("error", { message: "quotes stream down" })).text, "quotes stream down");
  assert.equal(describeEvent(ev("something.new", {})).text, "something.new");
});

test("money and R", () => {
  assert.equal(signedUsd(1234.5), "+$1,234.50");
  assert.equal(signedUsd(-3), "-$3.00");
  assert.equal(signedUsd(0), "$0.00");
  assert.equal(signedR(0.5), "+0.50R");
  assert.equal(signedR(null), "");
});

const pos = (side: "long" | "short", price: number, stop: number | null, target: number | null): PaperPosition => ({
  symbol: "SPY", side, qty: 10, avg_entry: 100, price, pnl: 0, r: null,
  trade: { id: 1, run_id: "r", stop, target, entry_ts: null, adopted: false, flat_by: null, mae_r: null, mfe_r: null },
});

test("bracket progress runs from the stop to the target, both sides", () => {
  assert.deepEqual(bracketProgress(pos("long", 104, 96, 112)), { price: 0.5, entry: 0.25 });
  assert.deepEqual(bracketProgress(pos("short", 96, 104, 88)), { price: 0.5, entry: 0.25 });
  assert.deepEqual(bracketProgress(pos("long", 150, 96, 112)), { price: 1, entry: 0.25 });
  assert.equal(bracketProgress(pos("long", 100, null, 112)), null);
});

test("countdown", () => {
  const now = Date.parse("2026-10-30T15:26:48-04:00");
  assert.equal(countdown("2026-10-30T15:30:00-04:00", now), "3:12");
  assert.equal(countdown("2026-10-30T17:30:05-04:00", now), "2:03:17");
  assert.equal(countdown("2026-10-30T15:00:00-04:00", now), null);
  assert.equal(countdown(null, now), null);
});
