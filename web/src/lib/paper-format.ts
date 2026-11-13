import type { PaperEvent, PaperPosition } from "@/lib/schemas/paper";

/* One line of text per paperbot event for the live feed, plus small helpers
   the Paper desk panels share. Pure: unit-tested in paper-format.test.ts. */

export type Tone = "up" | "down" | "warn" | "info" | "muted";

type Payload = Record<string, unknown>;

const num = (p: Payload, k: string): number | null => (typeof p[k] === "number" ? (p[k] as number) : null);
const str = (p: Payload, k: string): string | null => (typeof p[k] === "string" ? (p[k] as string) : null);
const obj = (p: Payload, k: string): Payload =>
  p[k] && typeof p[k] === "object" ? (p[k] as Payload) : {};

export const px = (x: number | null | undefined): string => (x == null ? "—" : x.toFixed(2));

export function signedUsd(x: number): string {
  const s = Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${x > 0 ? "+" : x < 0 ? "-" : ""}$${s}`;
}

export function signedR(r: number | null | undefined): string {
  return r == null ? "" : `${r > 0 ? "+" : ""}${r.toFixed(2)}R`;
}

/** "10:15:02" from an ISO timestamp. */
export function clock(ts: string): string {
  return ts.length >= 19 ? ts.slice(11, 19) : ts;
}

export function describeEvent(e: PaperEvent): { text: string; tone: Tone } {
  const p = e.payload;
  const sym = e.symbol ?? "";
  switch (e.type) {
    case "fill": {
      if (str(p, "kind") === "entry") {
        return { text: `Entered ${str(p, "side")} ${num(p, "qty")} ${sym} @ ${px(num(p, "entry_price"))}`, tone: "info" };
      }
      return { text: `${str(p, "kind")} fill ${num(p, "qty") ?? ""} ${sym} @ ${px(num(p, "price"))}`, tone: "muted" };
    }
    case "trade.closed": {
      const pnl = num(p, "pnl") ?? 0;
      const r = signedR(num(p, "r"));
      return {
        text: `Closed ${str(p, "side")} ${num(p, "qty")} ${sym} by ${str(p, "exit_reason")} @ ${px(num(p, "exit_price"))}: ${signedUsd(pnl)}${r ? ` (${r})` : ""}`,
        tone: pnl >= 0 ? "up" : "down",
      };
    }
    case "decision": {
      const outcome = str(p, "outcome");
      return { text: str(p, "summary") ?? `decision ${outcome}`, tone: outcome === "entered" ? "info" : "warn" };
    }
    case "proposal.created":
      return { text: `Proposal #${num(p, "id")}: ${str(p, "ticket")}`, tone: "warn" };
    case "proposal.approved":
      return { text: `Proposal #${num(p, "id")} approved`, tone: "info" };
    case "proposal.rejected":
      return { text: `Proposal #${num(p, "id")} rejected`, tone: "muted" };
    case "proposal.expired":
      return { text: `Proposal #${num(p, "id")} expired: ${str(p, "reason")}`, tone: "muted" };
    case "order.submitted": {
      const o = obj(p, "order");
      return { text: `Order ${str(p, "kind")}: ${str(o, "side")} ${num(o, "qty")} ${sym} (${str(o, "type")})`, tone: "muted" };
    }
    case "order.canceled":
    case "order.rejected":
      return { text: `Order ${str(p, "event") ?? e.type.slice(6)} ${sym}${str(p, "reason") ? `: ${str(p, "reason")}` : ""}`, tone: e.type === "order.rejected" ? "down" : "muted" };
    case "position.opened":
      return { text: `Position opened: ${str(p, "side")} ${num(p, "qty")} ${sym} @ ${px(num(p, "entry_price"))}${p.offline ? " (while offline)" : ""}`, tone: "info" };
    case "position.adopted":
      return { text: `Adopted ${str(p, "side")} ${num(p, "qty")} ${sym} @ ${px(num(p, "avg_entry"))} - protection: ${str(p, "protection")}${p.protective_stop_moved ? " (stop moved)" : ""}`, tone: "warn" };
    case "command": {
      const result = obj(p, "result");
      const status = str(p, "status");
      return {
        text: `Command ${str(p, "kind")}: ${status}${status === "error" && str(result, "error") ? ` - ${str(result, "error")}` : ""}`,
        tone: status === "error" ? "down" : "muted",
      };
    }
    case "swing.pass":
      return { text: `Swing pass ${str(p, "day")}: ${num(p, "evaluated") ?? 0} evaluated, ${num(p, "entries") ?? 0} entries, ${num(p, "exits") ?? 0} exits`, tone: "muted" };
    case "error":
      return { text: str(p, "message") ?? "error", tone: "down" };
    case "bot.start":
      return { text: `Bot started (pid ${num(p, "pid")})`, tone: "info" };
    case "bot.stop":
      return { text: `Bot stopped (pid ${num(p, "pid")})`, tone: "warn" };
    case "bot.paused":
      return { text: "Paused - no new entries", tone: "warn" };
    case "bot.resumed":
      return { text: "Resumed", tone: "info" };
    case "bot.config":
      return { text: `Config changed: approval ${str(p, "approval_mode")}, LLM ${str(p, "llm_mode")}`, tone: "muted" };
    default:
      return { text: e.type, tone: "muted" };
  }
}

/** Where the price sits between stop (0) and target (1), plus the entry mark. */
export function bracketProgress(pos: PaperPosition): { price: number; entry: number } | null {
  const stop = pos.trade?.stop;
  const target = pos.trade?.target;
  if (stop == null || target == null || stop === target) return null;
  const at = (x: number) => Math.min(1, Math.max(0, (x - stop) / (target - stop)));
  return { price: at(pos.price), entry: at(pos.avg_entry) };
}

/** "3:12" left until `iso`, or null when it has passed. */
export function countdown(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null;
  const ms = Date.parse(iso) - now;
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
