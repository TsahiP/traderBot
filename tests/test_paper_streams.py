"""paper.streams: normalisers, reconnect with backoff, quote subscriptions and
throttling - against fake stream objects, no network."""
import asyncio
import queue
import threading
import time
from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce

from paper.streams import (QuoteStreamThread, StreamThread, TradingStreamThread, normalize_quote,
                           normalize_trade_update, symbol_diff)

UTC = ZoneInfo("UTC")


def _wait(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


class FakeStream:
    """Stands in for TradingStream / StockDataStream: run() blocks until stop()."""

    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self._running = False
        self._stopped = threading.Event()
        self.trade_handler = None
        self.update_handler = None
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    def subscribe_trade_updates(self, handler):
        self.update_handler = handler

    def subscribe_trades(self, handler, *symbols):
        self.trade_handler = handler
        self.subscribed.extend(symbols)

    def unsubscribe_trades(self, *symbols):
        self.unsubscribed.extend(symbols)

    def run(self):
        if self.fail:
            raise self.fail
        self._running = True
        self._stopped.wait()
        self._running = False

    def stop(self):
        self._stopped.set()


def _order(**over):
    base = dict(id="abc", client_order_id="c1", symbol="SPY", side=OrderSide.BUY, qty="10",
                filled_qty="10", filled_avg_price="100.25", type=NS(value="market"),
                order_class=OrderClass.BRACKET, time_in_force=TimeInForce.GTC,
                status=NS(value="filled"), limit_price=None, stop_price=None,
                submitted_at=datetime(2026, 10, 30, 14, 15, tzinfo=UTC), legs=None)
    base.update(over)
    return NS(**base)


# ---------------------------------------------------------------- normalisers

def test_normalize_trade_update():
    raw = NS(event=NS(value="fill"), execution_id="e1", order=_order(), price=100.25, qty=10,
             position_qty=10, timestamp=datetime(2026, 10, 30, 14, 15, 2, tzinfo=UTC))
    out = normalize_trade_update(raw)
    assert out["kind"] == "trade_update" and out["event"] == "fill"
    assert out["order"]["id"] == "abc" and out["order"]["filled_avg"] == 100.25
    assert (out["price"], out["qty"], out["position_qty"]) == (100.25, 10.0, 10.0)
    assert out["timestamp"] == "2026-10-30T10:15:02-04:00" and out["execution_id"] == "e1"


def test_normalize_trade_update_without_a_fill():
    raw = NS(event="canceled", execution_id=None, order=_order(status=NS(value="canceled")),
             price=None, qty=None, position_qty=None, timestamp=datetime(2026, 10, 30, 14, 0, tzinfo=UTC))
    out = normalize_trade_update(raw)
    assert out["event"] == "canceled" and out["price"] is None and out["execution_id"] is None


def test_normalize_quote():
    raw = NS(symbol="SPY", price=101.5, size=100, timestamp=datetime(2026, 10, 30, 14, 15, tzinfo=UTC))
    assert normalize_quote(raw) == {"kind": "quote", "symbol": "SPY", "price": 101.5, "size": 100.0,
                                    "timestamp": "2026-10-30T10:15:00-04:00"}


def test_symbol_diff():
    assert symbol_diff({"SPY", "QQQ"}, {"QQQ", "NVDA", "AMD"}) == (["AMD", "NVDA"], ["SPY"])
    assert symbol_diff(set(), set()) == ([], [])


# ---------------------------------------------------------------- reconnect

def test_a_failed_run_is_retried_with_a_new_stream():
    out = queue.Queue()
    streams = [FakeStream(fail=ConnectionError("dns")), FakeStream()]
    made = []

    def factory():
        s = streams[len(made)]
        made.append(s)
        return s

    t = StreamThread("trading", factory, out, backoff=(0.0,))
    t.start()
    assert _wait(lambda: len(made) == 2 and made[1]._running)
    assert t.health() == "up"
    t.stop()
    assert _wait(lambda: not t.is_alive())
    statuses = [(m["status"], m["detail"]) for m in _drain(out) if m["kind"] == "stream_status"]
    assert statuses[0][0] == "connecting"
    assert any(s == "down" and "dns" in d and "retry" in d for s, d in statuses)
    assert statuses[-1][0] == "stopped"
    assert t.health() == "stopped"


def test_trading_updates_reach_the_queue():
    out = queue.Queue()
    stream = FakeStream()
    t = TradingStreamThread(out, factory=lambda: stream)
    t.start()
    assert _wait(lambda: stream._running)
    raw = NS(event=NS(value="fill"), execution_id="e1", order=_order(), price=100.25, qty=10,
             position_qty=10, timestamp=datetime(2026, 10, 30, 14, 15, tzinfo=UTC))
    asyncio.run(stream.update_handler(raw))
    t.stop()
    updates = [m for m in _drain(out) if m["kind"] == "trade_update"]
    assert updates and updates[0]["order"]["id"] == "abc"


def test_a_bad_update_becomes_an_error_item_not_a_crash():
    out = queue.Queue()
    stream = FakeStream()
    t = TradingStreamThread(out, factory=lambda: stream)
    t.start()
    assert _wait(lambda: stream._running)
    asyncio.run(stream.update_handler(NS(event="fill")))      # no order at all
    t.stop()
    assert any(m["kind"] == "stream_error" for m in _drain(out))


# ---------------------------------------------------------------- quotes

def test_quote_stream_runs_only_while_there_are_symbols():
    out = queue.Queue()
    made = []

    def factory():
        made.append(FakeStream())
        return made[-1]

    q = QuoteStreamThread(out, factory=factory)
    q.set_symbols(set())
    assert made == [] and q.health() == "idle"
    q.set_symbols({"SPY"})
    assert _wait(lambda: made and made[0]._running)
    assert made[0].subscribed == ["SPY"]
    q.set_symbols({"SPY", "QQQ"})
    assert made[0].subscribed == ["SPY", "QQQ"]
    q.set_symbols({"QQQ"})
    assert made[0].unsubscribed == ["SPY"]
    q.set_symbols(set())
    assert _wait(lambda: not made[0]._running) and q.health() == "idle"
    q.set_symbols({"NVDA"})
    assert _wait(lambda: len(made) == 2 and made[1]._running)
    assert made[1].subscribed == ["NVDA"]
    q.set_symbols(set())


def test_quotes_are_throttled_per_symbol():
    out = queue.Queue()
    clock = {"t": 100.0}
    stream = FakeStream()
    q = QuoteStreamThread(out, factory=lambda: stream, min_interval_s=1.0, clock=lambda: clock["t"])
    q.set_symbols({"SPY", "QQQ"})
    assert _wait(lambda: stream._running)

    def tick(sym, price):
        asyncio.run(stream.trade_handler(NS(symbol=sym, price=price, size=1,
                                             timestamp=datetime(2026, 10, 30, 14, 0, tzinfo=UTC))))

    tick("SPY", 100.0)
    tick("SPY", 100.1)          # same second: dropped
    tick("QQQ", 400.0)          # another symbol: kept
    clock["t"] += 1.0
    tick("SPY", 100.2)
    q.set_symbols(set())
    prices = [(m["symbol"], m["price"]) for m in _drain(out) if m["kind"] == "quote"]
    assert prices == [("SPY", 100.0), ("QQQ", 400.0), ("SPY", 100.2)]
