"""Executor: turns decisions into Alpaca orders, runs proposals, and gets out.

- `send` re-runs the whole funnel on a fresh context (fresh price, account,
  today's entries) in auto mode, then submits the bracket. A decision made at
  16:05 or a proposal approved minutes later cannot breach a limit that the
  numbers at send time would block. Swing runs send GTC legs, day runs DAY.
- `propose` stores a manual-mode decision as a pending proposal (outcome
  `proposed`, with its expiry and trigger price in `plan.proposal`);
  `approve` re-checks and sends it, `reject` drops it, `expire_due` times it
  out or expires it when the price drifts too far from the trigger (a late
  entry is a different trade). Turning the approval switch off never touches
  pending proposals.
- `exit_position` cancels the symbol's open orders (bracket legs hold the
  shares), waits for the cancels, then sends a market exit. Exits are never
  gated. Trades are opened and closed from fill events by the bot, not here.

Every step lands in the journal and on the bus (`decision`,
`order.submitted`, `proposal.*`, `error`).
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from paper.broker import exit_legs
from paper.funnel import Decision, FunnelContext, run_funnel
from paper.runs import Intent

logger = logging.getLogger("paperbot")

CANCEL_TRIES = 10
CANCEL_WAIT_S = 1.0


class ExecutorError(ValueError):
    """A command that cannot run (no such proposal, no such position)."""


@dataclass
class SendResult:
    ok: bool
    decision: Decision | None
    decision_id: int | None
    order: dict | None = None
    error: str | None = None


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class Executor:
    def __init__(self, broker, journal, bus, sleep=time.sleep,
                 cancel_tries: int = CANCEL_TRIES, cancel_wait_s: float = CANCEL_WAIT_S):
        self.broker = broker
        self.journal = journal
        self.bus = bus
        self._sleep = sleep
        self._cancel_tries = cancel_tries
        self._cancel_wait_s = cancel_wait_s

    # ------------------------------------------------------------ entries

    def send(self, intent: Intent, ctx: FunnelContext, *, now: datetime, config_hash: str) -> SendResult:
        """Re-check `intent` on `ctx` and send it now."""
        return self._execute(intent, ctx, now, config_hash, existing_id=None, announce_ready=True)

    def _record(self, decision: Decision, intent: Intent, now: datetime, config_hash: str,
                outcome: str, existing_id: int | None, failed_stage: str | None = None,
                extra_plan: dict | None = None) -> int:
        plan = dict(decision.plan or {})
        plan["intent"] = asdict(intent)
        if extra_plan:
            plan.update(extra_plan)
        if existing_id is None:
            fields = decision.journal_fields(ts=_iso(now), config_hash=config_hash, outcome=outcome)
            fields.update(plan=plan, failed_stage=failed_stage or decision.failed_stage)
            return self.journal.add_decision(**fields)
        keep = self.journal.decision(existing_id)["plan"] or {}
        if "proposal" in keep:
            plan["proposal"] = keep["proposal"]
        self.journal.update_decision(existing_id, outcome=outcome,
                                     failed_stage=failed_stage or decision.failed_stage,
                                     stages=decision.stages, plan=plan, qty=decision.qty,
                                     max_loss=decision.max_loss, score=decision.score)
        return existing_id

    def _execute(self, intent: Intent, ctx: FunnelContext, now: datetime, config_hash: str,
                 existing_id: int | None, announce_ready: bool) -> SendResult:
        ctx = replace(ctx, cfg={**ctx.cfg, "approval_mode": "auto"})
        decision = run_funnel(intent, ctx)
        ids = dict(run_id=intent.run_id, symbol=intent.symbol)
        if decision.outcome != "ready":
            did = self._record(decision, intent, now, config_hash, decision.outcome, existing_id)
            self.bus.publish("decision", {"id": did, "outcome": decision.outcome,
                                          "summary": decision.summary()}, **ids)
            return SendResult(False, decision, did, error=decision.summary())

        plan = decision.plan
        tif = "gtc" if ctx.run.style == "swing" else "day"
        try:
            order = self.broker.submit_bracket(
                intent.symbol, "buy" if intent.side == "long" else "sell", decision.qty,
                stop=plan["stop"], target=plan["target"], tif=tif,
                client_order_id=f"pb-{intent.run_id}-{intent.symbol}-{intent.signal_bar}-{intent.side}",
            )
        except Exception as exc:
            msg = f"{intent.symbol} bracket rejected: {exc}"
            did = self._record(decision, intent, now, config_hash, "rejected", existing_id,
                               failed_stage="execute", extra_plan={"error": str(exc)})
            self.bus.publish("decision", {"id": did, "outcome": "rejected", "summary": msg}, **ids)
            self.bus.publish("error", {"message": msg, "decision_id": did}, **ids)
            logger.error(msg)
            return SendResult(False, decision, did, error=msg)

        did = self._record(decision, intent, now, config_hash, "entered", existing_id)
        self._store_order(order, "entry", decision_id=did)
        for kind, leg in exit_legs(order).items():
            if leg is not None:
                self._store_order({**leg, "parent_id": order["id"]}, kind, decision_id=did)
        if announce_ready:
            self.bus.publish("decision", {"id": did, "outcome": "entered", "summary": decision.summary()},
                             **ids)
        self.bus.publish("order.submitted", {"order": order, "kind": "entry", "decision_id": did}, **ids)
        logger.info("%s - order %s", decision.summary(), order["id"])
        return SendResult(True, decision, did, order=order)

    def _store_order(self, order: dict, kind: str, decision_id: int | None = None) -> None:
        self.journal.upsert_order(
            order["id"], parent_id=order.get("parent_id"), decision_id=decision_id, kind=kind,
            side=order["side"], symbol=order["symbol"], qty=order["qty"], type=order["type"],
            tif=order["tif"], status=order["status"], limit_price=order.get("limit_price"),
            stop_price=order.get("stop_price"), filled_qty=order.get("filled_qty"),
            filled_avg=order.get("filled_avg"), submitted_at=order.get("submitted_at"),
        )

    # ------------------------------------------------------------ proposals

    def propose(self, intent: Intent, decision: Decision, *, now: datetime, config_hash: str,
                expires_at: datetime) -> dict:
        trigger = float(decision.plan["entry"]) if decision.plan else float(intent.ref_price)
        proposal = {"created_at": _iso(now), "expires_at": _iso(expires_at), "trigger_price": trigger}
        did = self._record(decision, intent, now, config_hash, "proposed", None,
                           extra_plan={"proposal": proposal})
        row = self.journal.decision(did)
        self.bus.publish("proposal.created", {"id": did, "ticket": decision.summary(),
                                              "plan": row["plan"], "stages": row["stages"],
                                              "qty": row["qty"], "max_loss": row["max_loss"],
                                              "expires_at": proposal["expires_at"]},
                         run_id=intent.run_id, symbol=intent.symbol)
        return row

    def pending_proposals(self) -> list[dict]:
        return self.journal.decisions_by_outcome("proposed")

    def _pending(self, proposal_id: int) -> dict:
        row = self.journal.decision(proposal_id)
        if row is None or row["outcome"] != "proposed":
            state = "not found" if row is None else row["outcome"]
            raise ExecutorError(f"proposal {proposal_id} is not pending ({state})")
        return row

    def approve(self, proposal_id: int, ctx: FunnelContext, *, now: datetime, config_hash: str) -> SendResult:
        row = self._pending(proposal_id)
        intent = Intent(**row["plan"]["intent"])
        self.bus.publish("proposal.approved", {"id": proposal_id}, run_id=intent.run_id, symbol=intent.symbol)
        ctx = replace(ctx, pending_symbols=set(ctx.pending_symbols) - {intent.symbol})
        return self._execute(intent, ctx, now, config_hash, existing_id=proposal_id, announce_ready=False)

    def reject(self, proposal_id: int, *, now: datetime) -> None:
        row = self._pending(proposal_id)
        self.journal.update_decision(proposal_id, outcome="rejected", failed_stage="approval")
        self.bus.publish("proposal.rejected", {"id": proposal_id, "at": _iso(now)},
                         run_id=row["run_id"], symbol=row["symbol"])

    def expire_due(self, now: datetime, prices: dict, max_drift_pct: float) -> list[dict]:
        """Expire proposals past their time, or whose price left the trigger by more than the drift."""
        expired = []
        for row in self.pending_proposals():
            meta = row["plan"]["proposal"]
            reason = None
            if now >= datetime.fromisoformat(meta["expires_at"]):
                reason = f"timed out at {datetime.fromisoformat(meta['expires_at']):%H:%M}"
            else:
                price, trigger = prices.get(row["symbol"]), meta["trigger_price"]
                if price and trigger:
                    drift = abs(price - trigger) / trigger * 100
                    if drift > max_drift_pct:
                        reason = f"price moved {drift:.2f}% from the trigger {trigger:.2f}"
            if reason:
                self.journal.update_decision(row["id"], outcome="expired", failed_stage="approval")
                self.bus.publish("proposal.expired", {"id": row["id"], "reason": reason},
                                 run_id=row["run_id"], symbol=row["symbol"])
                expired.append({"id": row["id"], "symbol": row["symbol"], "reason": reason})
        return expired

    # ------------------------------------------------------------ exits

    def exit_position(self, symbol: str, reason: str, *, now: datetime) -> dict:
        """Cancel the symbol's open orders, then a market exit. Never gated."""
        pos = next((p for p in self.broker.positions() if p["symbol"] == symbol), None)
        if pos is None:
            raise ExecutorError(f"no open position in {symbol}")
        for o in self.broker.open_orders(symbol):
            self.broker.cancel(o["id"])
        for _ in range(self._cancel_tries):
            if not self.broker.open_orders(symbol):
                break
            self._sleep(self._cancel_wait_s)
        else:
            logger.warning("Open orders for %s still pending cancel - exiting anyway", symbol)
        side = "sell" if pos["side"] == "long" else "buy"
        order = self.broker.submit_market(symbol, side, pos["qty"], "day")
        self._store_order(order, reason)
        self.bus.publish("order.submitted", {"order": order, "kind": reason, "at": _iso(now)}, symbol=symbol)
        logger.info("Exit %s %s x%s (%s) - order %s", side, symbol, pos["qty"], reason, order["id"])
        return order

    def close_position(self, symbol: str, *, now: datetime) -> dict:
        return self.exit_position(symbol, "manual", now=now)

    def flatten_all(self, *, now: datetime) -> list[dict]:
        orders = []
        for pos in self.broker.positions():
            try:
                orders.append(self.exit_position(pos["symbol"], "flatten_all", now=now))
            except Exception as exc:
                msg = f"flatten {pos['symbol']} failed: {exc}"
                logger.error(msg)
                self.bus.publish("error", {"message": msg}, symbol=pos["symbol"])
        return orders
