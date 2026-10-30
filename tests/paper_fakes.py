"""In-memory stand-in for paper.broker.Broker, shaped like Alpaca's paper account.

Mirrors the real order shapes so code under test reads them the same way:
- a bracket is a parent entry order with two legs (limit = target, stop =
  stop), held until the parent fills;
- an OCO is a parent limit order (the target) with one stop leg.
`fill()` fills a pending order, `hit()` fills a stop or target and cancels its
sibling, `fail_next()` makes the next call of a method raise.
"""
from __future__ import annotations

import itertools
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from paper import config as pcfg
from paper.broker import OPEN_STATUSES, exit_legs
from paper.funnel import FunnelContext
from paper.runs import Intent, Run

TS = "2026-10-30T10:15:00-04:00"
NY = ZoneInfo("America/New_York")
DECIDED_AT = datetime(2026, 10, 29, 16, 5, tzinfo=NY)    # swing pass after the close
ENTRY_AT = datetime(2026, 10, 30, 9, 45, tzinfo=NY)      # its order goes out next morning


def flat_bars(n: int = 60, rng: float = 2.0, close: float = 100.0) -> pd.DataFrame:
    """Flat daily closes with a constant high-low range, so ATR14 == rng."""
    idx = pd.date_range("2026-08-01", periods=n, freq="B", tz=NY)
    c = np.full(n, close)
    return pd.DataFrame({"Open": c, "High": c + rng / 2, "Low": c - rng / 2, "Close": c,
                         "Volume": np.full(n, 1e6)}, index=idx)


def swing_run(**over) -> Run:
    raw = next(r for r in pcfg.default_config()["runs"] if r["id"] == "swing_sma")
    return Run.from_config({**raw, **over})


def entry_intent(side: str = "long", symbol: str = "SPY", ref_price: float = 100.0) -> Intent:
    return Intent("entry", "swing_sma", symbol, side, "2026-10-29", ref_price,
                  "SMA 10 crossed above SMA 50")


def funnel_ctx(**over) -> FunnelContext:
    """A context where a long SPY swing entry at 100 passes: ATR 2 -> stop 96,
    target 108, 50 shares by the $200 max loss."""
    base = dict(
        now_ny=DECIDED_AT, entry_at=ENTRY_AT, cfg=pcfg.default_config(), run=swing_run(),
        universe={"SPY"}, account={"equity": 100_000.0, "cash": 100_000.0, "buying_power": 200_000.0},
        positions=[], pending_symbols=set(), entries_today=[], run_entries_today=[],
        open_risk_usd=0.0, short_exposure_usd=0.0,
        asset={"symbol": "SPY", "tradable": True, "shortable": True, "easy_to_borrow": True},
        bars=flat_bars(), price=100.0,
    )
    base.update(over)
    return FunnelContext(**base)


class FakeBroker:
    def __init__(self, equity: float = 100_000.0, cash: float | None = None,
                 buying_power: float | None = None, is_open: bool = True):
        self.equity = equity
        self.cash = equity if cash is None else cash
        self.buying_power = equity * 2 if buying_power is None else buying_power
        self.is_open = is_open
        self.prices: dict[str, float] = {}
        self.bars: dict[str, pd.DataFrame] = {}
        self.assets: dict[str, dict] = {}
        self._positions: dict[str, dict] = {}
        self._orders: dict[str, dict] = {}
        self._ids = itertools.count(1)
        self._fail: dict[str, Exception] = {}
        self.auto_fill_market = False
        self.opened_today: set[tuple[str, str]] = set()      # (symbol, "buy" | "sell")
        self.calls: list[tuple] = []

    # ------------------------------------------------------------ test controls

    def fail_next(self, method: str, exc: Exception) -> None:
        self._fail[method] = exc

    def _check(self, method: str, *args) -> None:
        self.calls.append((method, *args))
        if method in self._fail:
            raise self._fail.pop(method)

    def set_position(self, symbol: str, side: str, qty: float, avg_entry: float) -> None:
        self._positions[symbol] = {"symbol": symbol, "side": side, "qty": float(qty),
                                   "avg_entry": float(avg_entry)}

    def _new_order(self, symbol, side, qty, type, order_class="simple", tif="day",
                   limit_price=None, stop_price=None, status="new", legs=None) -> dict:
        oid = f"o{next(self._ids)}"
        order = {"id": oid, "client_order_id": f"c-{oid}", "symbol": symbol, "side": side,
                 "qty": float(qty), "filled_qty": 0.0, "filled_avg": None, "type": type,
                 "order_class": order_class, "tif": tif, "status": status,
                 "limit_price": limit_price, "stop_price": stop_price, "submitted_at": TS,
                 "legs": legs or []}
        self._orders[oid] = order
        return order

    def _apply_fill(self, order: dict, price: float) -> None:
        order["status"], order["filled_qty"], order["filled_avg"] = "filled", order["qty"], float(price)
        symbol, qty = order["symbol"], order["qty"]
        pos = self._positions.get(symbol)
        opening_side = "long" if order["side"] == "buy" else "short"
        if pos is None:
            self.set_position(symbol, opening_side, qty, price)
        elif pos["side"] == opening_side:
            total = pos["qty"] + qty
            pos["avg_entry"] = (pos["avg_entry"] * pos["qty"] + price * qty) / total
            pos["qty"] = total
        else:
            pos["qty"] -= qty
            if pos["qty"] <= 1e-9:
                del self._positions[symbol]

    def fill(self, order_id: str, price: float | None = None) -> None:
        """Fill a pending order (entry or exit). A bracket's legs go live."""
        order = self._orders[order_id]
        if order["status"] not in OPEN_STATUSES:
            raise AssertionError(f"{order_id} is {order['status']}, not open")
        self._apply_fill(order, price if price is not None else self.prices[order["symbol"]])
        if order["order_class"] == "bracket":
            for leg in order["legs"]:
                leg["status"] = "new"

    def hit(self, order_id: str, kind: str, price: float) -> None:
        """A bracket/OCO exit fills at `price`; the other side is canceled."""
        legs = exit_legs(self._orders[order_id])
        filled, other = legs[kind], legs["target" if kind == "stop" else "stop"]
        if filled is None or filled["status"] not in OPEN_STATUSES:
            raise AssertionError(f"{kind} of {order_id} is not live")
        self._apply_fill(filled, price)
        if other is not None and other["status"] in OPEN_STATUSES:
            other["status"] = "canceled"

    def _all_orders(self):
        for order in self._orders.values():
            yield order
            yield from order["legs"]

    # ------------------------------------------------------------ Broker interface

    def clock(self) -> dict:
        self._check("clock")
        return {"is_open": self.is_open, "timestamp": TS, "next_open": None, "next_close": None}

    def account(self) -> dict:
        self._check("account")
        return {"equity": self.equity, "cash": self.cash, "buying_power": self.buying_power,
                "shorting_enabled": True}

    def positions(self) -> list[dict]:
        self._check("positions")
        out = []
        for pos in self._positions.values():
            price = self.prices.get(pos["symbol"], pos["avg_entry"])
            sign = 1 if pos["side"] == "long" else -1
            out.append({**pos, "current_price": price, "market_value": sign * price * pos["qty"],
                        "unrealized_pl": round(sign * (price - pos["avg_entry"]) * pos["qty"], 2)})
        return out

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        self._check("open_orders", symbol)
        return [{**o, "legs": []} for o in self._all_orders()
                if o["status"] in OPEN_STATUSES and (symbol is None or o["symbol"] == symbol)]

    def order(self, order_id: str) -> dict:
        self._check("order", order_id)
        for o in self._all_orders():
            if o["id"] == order_id:
                return o
        raise KeyError(order_id)

    def filled_today(self, symbol: str, side: str, now) -> bool:
        self._check("filled_today", symbol, side)
        return (symbol, side) in self.opened_today

    def asset(self, symbol: str) -> dict:
        self._check("asset", symbol)
        return self.assets.get(symbol, {"symbol": symbol, "tradable": True, "shortable": True,
                                        "easy_to_borrow": True})

    def submit_bracket(self, symbol: str, side: str, qty: int, stop: float, target: float,
                       tif: str = "gtc", client_order_id: str | None = None) -> dict:
        self._check("submit_bracket", symbol, side, qty, stop, target, tif)
        exit_side = "sell" if side == "buy" else "buy"
        legs = [self._leg(symbol, exit_side, qty, "limit", tif, limit_price=target),
                self._leg(symbol, exit_side, qty, "stop", tif, stop_price=stop)]
        order = self._new_order(symbol, side, qty, "market", "bracket", tif, legs=legs)
        if client_order_id:
            order["client_order_id"] = client_order_id
        return order

    def _leg(self, symbol, side, qty, type, tif, limit_price=None, stop_price=None, status="held"):
        oid = f"o{next(self._ids)}"
        return {"id": oid, "client_order_id": f"c-{oid}", "symbol": symbol, "side": side,
                "qty": float(qty), "filled_qty": 0.0, "filled_avg": None, "type": type,
                "order_class": "simple", "tif": tif, "status": status, "limit_price": limit_price,
                "stop_price": stop_price, "submitted_at": TS, "legs": []}

    def submit_oco(self, symbol: str, exit_side: str, qty: float, stop: float, target: float) -> dict:
        self._check("submit_oco", symbol, exit_side, qty, stop, target)
        stop_leg = self._leg(symbol, exit_side, qty, "stop", "gtc", stop_price=stop, status="new")
        return self._new_order(symbol, exit_side, qty, "limit", "oco", "gtc",
                               limit_price=target, legs=[stop_leg])

    def submit_market(self, symbol: str, side: str, qty: float, tif: str = "day") -> dict:
        self._check("submit_market", symbol, side, qty, tif)
        order = self._new_order(symbol, side, qty, "market", tif=tif)
        if self.auto_fill_market:
            self.fill(order["id"])
        return order

    def cancel(self, order_id: str) -> None:
        self._check("cancel", order_id)
        order = self.order(order_id)
        if order["status"] in OPEN_STATUSES:
            order["status"] = "canceled"
            for leg in order["legs"]:
                if leg["status"] in OPEN_STATUSES:
                    leg["status"] = "canceled"

    def latest_price(self, symbol: str) -> float | None:
        self._check("latest_price", symbol)
        return self.prices.get(symbol)

    def daily_bars(self, symbol: str, now, days: int = 400) -> pd.DataFrame:
        self._check("daily_bars", symbol)
        return self.bars[symbol]
