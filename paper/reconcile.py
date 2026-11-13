"""Startup reconcile: line the journal up with Alpaca before the bot acts (spec §5.8).

The bot may have been off while the market moved: brackets live at Alpaca and
keep working. Run on every start (and safe to run again - it is idempotent):

1. Entry orders the journal has as open: still open -> pending (re-attached);
   filled -> the trade is opened from its decision (stop, target, run);
   dead without a fill -> marked, and the decision stops counting as an entry.
2. Open trades with no position left: the closing fill is found in the trade's
   orders (bracket leg / OCO side -> stop or target; a market exit -> its
   reason) and the trade is closed there; no trace -> closed at the last price
   with exit_reason `broker`. A position whose size changed outside the bot
   updates the trade's qty.
3. Positions the journal does not know are ADOPTED (run_id `adopted`):
   - with their own stop / target orders at Alpaca -> those levels, no new orders;
   - naked -> a protective OCO at avg entry -/+ 2 x ATR14 (daily) and 2R;
     if the price is already past that stop, the stop goes 1 x ATR from the
     current price instead (`protective_stop_moved`);
   - opened today -> also flattened at flat-by; older ones are swing (GTC).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from paper import exit_plan
from paper.broker import OPEN_STATUSES, exit_legs
from paper.executor import record_order

logger = logging.getLogger("paperbot")

ADOPT_STOP_RULE = "atr:2"
ADOPT_MOVED_STOP_RULE = "atr:1"
ADOPT_TARGET_R = 2.0
EXIT_KINDS = {"signal", "manual", "flatten_all", "flat_by", "flatten"}


@dataclass
class ReconcileReport:
    opened: list = field(default_factory=list)      # trade ids opened from entries filled offline
    closed: list = field(default_factory=list)      # trade ids closed from exits filled offline
    adopted: list = field(default_factory=list)     # trade ids of adopted positions
    pending: list = field(default_factory=list)     # symbols with an entry still working
    errors: list = field(default_factory=list)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _filled(order: dict) -> bool:
    return order["status"] == "filled" or (order.get("filled_qty") or 0) > 0


def _sync_order(journal, order: dict) -> None:
    journal.upsert_order(order["id"], status=order["status"], filled_qty=order.get("filled_qty"),
                         filled_avg=order.get("filled_avg"))
    for leg in order.get("legs") or []:
        if journal.order(leg["id"]):
            _sync_order(journal, leg)


def _check_entries(broker, journal, bus, report: ReconcileReport, now: datetime) -> None:
    open_symbols = {t["symbol"] for t in journal.open_trades()}
    for row in journal.orders(statuses=tuple(OPEN_STATUSES)):
        if row["kind"] != "entry":
            continue
        order = broker.order(row["alpaca_id"])
        _sync_order(journal, order)
        symbol = row["symbol"]
        if order["status"] in OPEN_STATUSES and not _filled(order):
            report.pending.append(symbol)
            continue
        if not _filled(order):
            if row["decision_id"]:
                journal.update_decision(row["decision_id"], outcome="expired", failed_stage="execute")
            bus.publish("order.canceled", {"order_id": order["id"], "status": order["status"],
                                           "reason": "entry ended without a fill"}, symbol=symbol)
            continue
        if symbol in open_symbols:
            continue
        decision = journal.decision(row["decision_id"]) if row["decision_id"] else None
        plan = (decision or {}).get("plan") or {}
        side = "long" if order["side"] == "buy" else "short"
        tid = journal.open_trade(
            run_id=(decision or {}).get("run_id") or "adopted", symbol=symbol, side=side,
            qty=order["filled_qty"] or order["qty"], entry_ts=order.get("submitted_at") or _iso(now),
            entry_price=order["filled_avg"], stop=plan.get("stop"), target=plan.get("target"),
            decision_id=row["decision_id"], score=(decision or {}).get("score"),
            llm_mode=(decision or {}).get("llm_mode"), llm_verdict=(decision or {}).get("llm_verdict"),
            config_hash=(decision or {}).get("config_hash"),
            flat_by=plan.get("flat_by"),
        )
        journal.upsert_order(order["id"], trade_id=tid)
        open_symbols.add(symbol)
        report.opened.append(tid)
        bus.publish("position.opened", {"trade_id": tid, "side": side, "qty": order["filled_qty"],
                                        "entry_price": order["filled_avg"], "stop": plan.get("stop"),
                                        "target": plan.get("target"), "offline": True},
                    run_id=(decision or {}).get("run_id"), symbol=symbol)


def _closing_fill(broker, journal, trade: dict) -> tuple[float, str] | None:
    """(price, reason) of the order that closed `trade`, if the journal can find it."""
    exit_side = "sell" if trade["side"] == "long" else "buy"
    rows = journal.orders_matching(symbol=trade["symbol"], since=trade["entry_ts"],
                                   decision_id=trade["decision_id"], trade_id=trade["id"])
    for row in reversed(rows):
        try:
            order = broker.order(row["alpaca_id"])
        except Exception:
            continue
        if row["kind"] in ("entry", "protective"):
            for kind, leg in exit_legs(order).items():
                if leg is not None and _filled(leg):
                    return float(leg["filled_avg"]), kind
        elif row["kind"] in ("stop", "target") or row["kind"] in EXIT_KINDS:
            if _filled(order) and order["side"] == exit_side:
                return float(order["filled_avg"]), row["kind"]
    return None


def _check_trades(broker, journal, bus, positions: dict, report: ReconcileReport, now: datetime) -> None:
    for trade in journal.open_trades():
        pos = positions.get(trade["symbol"])
        if pos is not None:
            if pos["qty"] != trade["qty"]:
                journal.update_trade(trade["id"], qty=pos["qty"])
            continue
        found = _closing_fill(broker, journal, trade)
        if found:
            price, reason = found
        else:
            price = broker.latest_price(trade["symbol"]) or trade["entry_price"]
            reason = "broker"
            logger.warning("%s closed outside the bot - recording at %.2f", trade["symbol"], price)
        closed = journal.close_trade(trade["id"], exit_ts=_iso(now), exit_price=price, exit_reason=reason)
        report.closed.append(trade["id"])
        bus.publish("trade.closed", {"trade_id": trade["id"], "exit_reason": reason, "exit_price": price,
                                     "pnl": closed["pnl"], "r": closed["r"], "offline": True},
                    run_id=trade["run_id"], symbol=trade["symbol"])


def _adopt(broker, journal, bus, pos: dict, report: ReconcileReport, now: datetime, flat_by: str) -> None:
    symbol, side, qty = pos["symbol"], pos["side"], pos["qty"]
    exit_side = "sell" if side == "long" else "buy"
    existing = [o for o in broker.open_orders(symbol) if o["side"] == exit_side]
    stop = next((o["stop_price"] for o in existing if o["type"] in ("stop", "stop_limit")), None)
    target = next((o["limit_price"] for o in existing if o["type"] == "limit"), None)
    opened_today = broker.filled_today(symbol, "buy" if side == "long" else "sell", now)
    protection, moved, oco, error = "existing", False, None, None

    if not existing:
        protection = "none"
        bars = broker.daily_bars(symbol, now)
        p = exit_plan.plan(side, pos["avg_entry"], ADOPT_STOP_RULE, ADOPT_TARGET_R, bars=bars)
        if isinstance(p, exit_plan.Refusal):
            error = f"{symbol} adopted without protection: {p.reason}"
        else:
            stop, target = p.stop, p.target
            price = pos.get("current_price") or broker.latest_price(symbol) or pos["avg_entry"]
            past = price <= stop if side == "long" else price >= stop
            if past:
                p2 = exit_plan.plan(side, price, ADOPT_MOVED_STOP_RULE, ADOPT_TARGET_R, bars=bars)
                if isinstance(p2, exit_plan.Refusal):
                    error, stop, target = f"{symbol} adopted without protection: {p2.reason}", None, None
                else:
                    stop, moved = p2.stop, True
            if stop is not None:
                oco = broker.submit_oco(symbol, exit_side, qty, stop=stop, target=target)
                protection = "oco"

    tid = journal.open_trade(run_id="adopted", symbol=symbol, side=side, qty=qty, entry_ts=_iso(now),
                             entry_price=pos["avg_entry"], stop=stop, target=target, adopted=True,
                             flat_by=flat_by if opened_today else None)
    for o in existing:
        record_order(journal, o, "stop" if o["type"] in ("stop", "stop_limit") else "target", trade_id=tid)
    if oco is not None:
        record_order(journal, oco, "protective", trade_id=tid)
    report.adopted.append(tid)
    bus.publish("position.adopted", {"trade_id": tid, "side": side, "qty": qty, "avg_entry": pos["avg_entry"],
                                     "stop": stop, "target": target, "protection": protection,
                                     "protective_stop_moved": moved,
                                     "flat_by": flat_by if opened_today else None},
                run_id="adopted", symbol=symbol)
    if error:
        report.errors.append(error)
        bus.publish("error", {"message": error, "trade_id": tid}, symbol=symbol)
        logger.error(error)


def reconcile(broker, journal, bus, *, now: datetime, flat_by: str) -> ReconcileReport:
    report = ReconcileReport()
    _check_entries(broker, journal, bus, report, now)
    positions = {p["symbol"]: p for p in broker.positions()}
    _check_trades(broker, journal, bus, positions, report, now)
    known = {t["symbol"] for t in journal.open_trades()} | set(report.pending)
    for symbol, pos in positions.items():
        if symbol in known:
            continue
        try:
            _adopt(broker, journal, bus, pos, report, now, flat_by)
        except Exception as exc:
            msg = f"could not adopt {symbol}: {exc}"
            report.errors.append(msg)
            bus.publish("error", {"message": msg}, symbol=symbol)
            logger.exception(msg)
    if report.opened or report.closed or report.adopted or report.errors:
        logger.info("Reconcile: opened %s, closed %s, adopted %s, pending %s, errors %s",
                    report.opened, report.closed, report.adopted, report.pending, len(report.errors))
    return report
