"""Day-skill board: the day-trader skill's path, off the paper desk.

The scanner comes first. Once a day it tags the US list (20-day breakout, bull
flag, MA20 reclaim, oversold, oversold bounce). After the opening range can be
read, only those names are checked for a long opening-range breakout: the first
completed 5-minute close above the 15-minute high, stop at the midpoint, target
2R, flat by 15:55. A PASS row is sent on its own. Paperbot's runs are not touched.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, time, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

import config
import market_calendar
import risk
import scanner
from paper import exit_plan
from paper.broker import Broker, BrokerError

ORDERS_PATH = config.OUTPUT_DIR / "skill_orders.json"
DAILY_PATH = config.OUTPUT_DIR / "skill_daily.json"
MAX_LOSS = 50.0
DAILY_LOSS = 150.0
MAX_TRADES = 3
POSITION_PCT = 75.0
MIN_VOLUME = 1_000_000
FLAT_BY = "15:55"
WINDOW_AFTER = "15:30"
CENT = Decimal("0.01")
MIN_RISK_PCT = Decimal("0.05")  # same floor as the exit planner: a 1-cent stop would size thousands of shares
RULE = ("scanner, then a long opening-range breakout: first 5-minute close above "
        "the 15-minute high, stop at the midpoint, target 2R, flat 15:55")
_lock = threading.Lock()
logger = logging.getLogger("skill_board")


def limits() -> risk.RiskLimits:
    return risk.RiskLimits(
        max_loss_per_trade_usd=MAX_LOSS, max_daily_loss_usd=DAILY_LOSS, max_trades_per_day=MAX_TRADES,
        min_reward_risk=1.0, max_position_pct=POSITION_PCT, cash_buffer_pct=10.0,
        no_entries_before="09:45", no_entries_after=WINDOW_AFTER,
    )


def load_orders() -> list[dict]:
    try:
        data = json.loads(ORDERS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_orders(rows: list[dict]) -> None:
    ORDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ORDERS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    tmp.replace(ORDERS_PATH)


def long_bracket(mid_stop: float, live: float) -> tuple[float, float, float]:
    """Market entry at the live price. Stop is the range midpoint, never at or above that price.

    Alpaca rejects a long bracket unless stop_price <= base_price - 0.01. When the
    midpoint is not under the live quote, the stop is pulled down to just under it,
    and at least 0.05% under so the share count stays inside the $50 loss. Target is 2R
    from that live entry and that stop.
    """
    entry = Decimal(str(round(float(live), 2)))
    stop = Decimal(str(round(float(mid_stop), 2)))
    min_risk = max((entry * MIN_RISK_PCT / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_UP), CENT)
    ceiling = entry - min_risk
    if stop > ceiling:
        stop = ceiling
    stop = stop.quantize(CENT, rounding=ROUND_FLOOR)
    if stop >= entry:
        stop = entry - CENT
    risk = entry - stop
    target = (entry + risk * 2).quantize(CENT, rounding=ROUND_HALF_UP)
    return float(entry), float(stop), float(target)


def _flat_at(day) -> datetime:
    hh, mm = (int(x) for x in FLAT_BY.split(":"))
    at = datetime.combine(day, time(hh, mm), market_calendar.NY)
    if day in market_calendar.early_closes(day.year):
        at -= timedelta(hours=3)
    return at


def _blank(symbol: str, status: str, sentence: str, setups: list[str]) -> dict:
    return {
        "symbol": symbol, "status": status, "sentence": sentence, "setups": setups,
        "range_high": None, "range_low": None, "last_close": None, "entry": None,
        "stop": None, "target": None, "qty": None, "max_loss": None,
        "verdict": None, "verdict_reason": None,
    }


def daily_hits(now: datetime, fetch) -> list[dict]:
    """One daily scan per New York date. Later checks reuse output/skill_daily.json."""
    day = now.date().isoformat()
    try:
        cached = json.loads(DAILY_PATH.read_text(encoding="utf-8"))
        if cached.get("day") == day and isinstance(cached.get("hits"), list):
            return cached["hits"]
    except (OSError, ValueError):
        pass
    symbols = [u["symbol"] for u in scanner.load_universe()]
    frames = fetch(symbols, period="3mo", interval="1d") if symbols else {}
    hits = []
    for sym, df in frames.items():
        if len(df) and df.index[-1].date() == now.date() and market_calendar.is_open(now):
            df = df.iloc[:-1]
        if len(df) < 21 or not scanner._liquid(df, scanner.MIN_PRICE, MIN_VOLUME):
            continue
        setups = scanner.daily_setups(df)
        if not setups:
            continue
        close = float(df["Close"].iloc[-1])
        vol = float(df["Volume"].iloc[-1] or 0)
        hits.append({"symbol": sym, "setups": setups, "close": round(close, 2),
                     "dollar_volume": round(vol * close)})
    hits.sort(key=lambda h: h["dollar_volume"], reverse=True)
    DAILY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DAILY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"day": day, "hits": hits}), encoding="utf-8")
    tmp.replace(DAILY_PATH)
    return hits


def evaluate_symbol(symbol: str, df, now: datetime, account: dict | None, sent: list[dict],
                    setups: list[str] | None = None) -> dict:
    """One scanner name. `sent` is today's skill orders (for the daily loss count)."""
    setups = list(setups or [])
    state = scanner.orb_state(df, now=now, min_volume_x=0, no_entries_after=WINDOW_AFTER)
    row = _blank(symbol, state["status"], state["sentence"], setups)
    row.update(range_high=state["range_high"], range_low=state["range_low"], last_close=state["last_close"])
    if state["status"] != "triggered":
        return row
    planned = exit_plan.plan(
        "long", float(state["entry"]), "or-mid", 2.0,
        or_high=state["range_high"], or_low=state["range_low"], now_ny=now, flat_by=FLAT_BY,
    )
    if isinstance(planned, exit_plan.Refusal):
        row["verdict"] = "BLOCK"
        row["verdict_reason"] = planned.reason
        return row
    price = max(planned.entry, float(state["last_close"] or planned.entry))
    row.update(entry=round(price, 2), stop=planned.stop, target=planned.target)
    if any(o["symbol"] == symbol and not o.get("flattened") for o in sent):
        row["verdict"] = "sent"
        row["verdict_reason"] = "already sent today from this tab"
        return row
    acct = account or {"equity": 0.0, "cash": 0.0, "buying_power": 0.0}
    qty, limited = risk.size_position(
        entry=price, stop=planned.stop, max_loss_usd=MAX_LOSS,
        equity=acct["equity"], cash=acct["cash"], buying_power=acct.get("buying_power"),
        max_position_pct=POSITION_PCT, cash_buffer_pct=10.0, side="long",
    )
    loss = risk.max_loss(qty, price, planned.stop, "long")
    row.update(qty=qty, max_loss=loss)
    verdict = risk.check_entry(
        symbol=symbol, qty=qty, entry=price, stop=planned.stop, target=planned.target,
        now_ny=now, equity=acct["equity"], cash=acct["cash"], buying_power=acct.get("buying_power"),
        entries_today=[{"symbol": o["symbol"], "max_loss": o.get("max_loss") or 0} for o in sent],
        limits=limits(), side="long",
    )
    if account is None:
        row["verdict"] = "BLOCK"
        row["verdict_reason"] = "Alpaca paper account is not connected"
        return row
    row["verdict"] = "PASS" if verdict.ok else "BLOCK"
    note = f"{qty} shares, limited by {limited}, max loss ${loss:.2f}" if verdict.ok else verdict.summary()
    row["verdict_reason"] = note
    return row


def build_board(now: datetime | None = None, fetch=None, account: dict | None = None,
                universe: list[dict] | None = None) -> dict:
    """Rows are scanner names only. Before the range is ready, the daily setups; after, their ORB state."""
    now = (now or datetime.now(market_calendar.NY)).astimezone(market_calendar.NY)
    fetch = fetch or scanner._download
    hits = daily_hits(now, fetch) if universe is None else universe
    note = scanner.orb_not_ready(now)
    if note:
        rows = [_blank(h["symbol"], "setup", ", ".join(h.get("setups") or []), list(h.get("setups") or []))
                for h in hits]
        scan = "daily"
    else:
        symbols = [h["symbol"] for h in hits]
        frames = fetch(symbols, period="1d", interval="5m") if symbols else {}
        sent = [o for o in load_orders() if o.get("day") == now.date().isoformat()]
        rows = [evaluate_symbol(h["symbol"], frames.get(h["symbol"]), now, account, sent, h.get("setups"))
                for h in hits]
        scan = "orb"
        rows.sort(key=lambda r: (0 if r.get("verdict") == "sent" else 1 if r.get("status") == "triggered" else 2, r["symbol"]))
    return {
        "ts": now.isoformat(timespec="seconds"), "rule": RULE, "scan": scan, "note": note,
        "source": "US daily scan" if universe is None else "scanner",
        "symbols": len(hits), "rows": rows,
    }


def send_passes(broker: Broker, now: datetime | None = None, fetch=None,
                universe: list[dict] | None = None, board: dict | None = None) -> list[dict]:
    """Send a day bracket for every PASS row. Reuses `board` so it does not scan again."""
    now = (now or datetime.now(market_calendar.NY)).astimezone(market_calendar.NY)
    account = broker.account()
    if board is None:
        board = build_board(now, fetch=fetch, account=account, universe=universe)
    sent = []
    for row in board["rows"]:
        if row.get("verdict") != "PASS":
            continue
        try:
            placed = _submit_row(broker, row, now, account)
        except Exception as exc:
            row["verdict"] = "BLOCK"
            row["verdict_reason"] = str(exc)[:180]
            logger.warning("skill entry %s skipped: %s", row["symbol"], exc)
            continue
        row.update(verdict="sent", entry=placed["entry"], stop=placed["stop"], target=placed["target"],
                   qty=placed["qty"], max_loss=placed["max_loss"],
                   verdict_reason=f"sent {placed['qty']} shares, stop {placed['stop']}, target {placed['target']}")
        sent.append(placed)
    return sent


def _submit_row(broker: Broker, row: dict, now: datetime, account: dict) -> dict:
    """Price the bracket off the live quote and send it. Caller holds no scan."""
    with _lock:
        symbol = row["symbol"]
        live = None
        try:
            live = broker.latest_price(symbol)
        except Exception:
            live = None
        entry, stop, target = long_bracket(float(row["stop"]), float(live or row["entry"]))
        qty, limited = risk.size_position(
            entry=entry, stop=stop, max_loss_usd=MAX_LOSS, equity=account["equity"], cash=account["cash"],
            buying_power=account.get("buying_power"), max_position_pct=POSITION_PCT,
            cash_buffer_pct=10.0, side="long",
        )
        loss = risk.max_loss(qty, entry, stop, "long")
        verdict = risk.check_entry(
            symbol=symbol, qty=qty, entry=entry, stop=stop, target=target, now_ny=now,
            equity=account["equity"], cash=account["cash"], buying_power=account.get("buying_power"),
            entries_today=[{"symbol": o["symbol"], "max_loss": o.get("max_loss") or 0} for o in load_orders()
                           if o.get("day") == now.date().isoformat()],
            limits=limits(), side="long",
        )
        if not verdict.ok:
            raise ValueError(verdict.summary())
        order = broker.submit_bracket(
            symbol, "buy", qty, stop, target,
            tif="day", client_order_id=f"skill-{symbol}-{now.date().isoformat()}",
        )
        saved = load_orders()
        saved.append({
            "day": now.date().isoformat(), "symbol": symbol, "qty": qty,
            "entry": entry, "stop": stop, "target": target,
            "max_loss": loss, "order_id": order["id"], "flattened": False,
            "limited_by": limited,
        })
        _save_orders(saved)
        return {"order_id": order["id"], "symbol": symbol, "qty": qty,
                "entry": entry, "stop": stop, "target": target, "max_loss": loss}


def flatten_due(broker: Broker, now: datetime | None = None) -> list[str]:
    """Market-sell skill positions still open at the flat-by time. Other positions stay."""
    now = (now or datetime.now(market_calendar.NY)).astimezone(market_calendar.NY)
    if now < _flat_at(now.date()):
        return []
    with _lock:
        orders = load_orders()
        due = [o for o in orders if o.get("day") == now.date().isoformat() and not o.get("flattened")]
        if not due:
            return []
        held = {p["symbol"]: p for p in broker.positions() if p["side"] == "long"}
        done = []
        for o in due:
            pos = held.get(o["symbol"])
            if pos and float(pos["qty"]).is_integer():
                broker.submit_market(o["symbol"], "sell", int(pos["qty"]), tif="day")
            o["flattened"] = True
            done.append(o["symbol"])
        _save_orders(orders)
        return done
