"""paper.broker: the Alpaca adapter builds the right requests and normalises
Alpaca objects to plain dicts; the fake broker follows the same contract."""
from datetime import datetime
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce

from paper import broker as pbroker
from paper.broker import Broker, exit_legs, normalize_order, normalize_position
from paper_fakes import FakeBroker

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 30, 10, 30, tzinfo=NY)


# ---------------------------------------------------------------- fake contract

def test_fake_bracket_long_fills_then_stops_out():
    fb = FakeBroker()
    order = fb.submit_bracket("SPY", "buy", 10, stop=95.0, target=110.0)
    legs = exit_legs(order)
    assert order["order_class"] == "bracket" and order["status"] == "new"
    assert legs["stop"]["status"] == legs["target"]["status"] == "held"
    assert legs["stop"]["side"] == "sell" and legs["target"]["limit_price"] == 110.0

    fb.fill(order["id"], 100.0)
    pos = fb.positions()[0]
    assert (pos["side"], pos["qty"], pos["avg_entry"]) == ("long", 10.0, 100.0)
    assert {o["type"] for o in fb.open_orders("SPY")} == {"limit", "stop"}

    fb.hit(order["id"], "stop", 94.9)
    assert fb.positions() == []
    assert legs["stop"]["filled_avg"] == 94.9 and legs["target"]["status"] == "canceled"
    assert fb.open_orders() == []


def test_fake_bracket_short_and_target():
    fb = FakeBroker()
    order = fb.submit_bracket("NVDA", "sell", 5, stop=185.8, target=175.3)
    assert exit_legs(order)["stop"]["side"] == "buy"
    fb.fill(order["id"], 182.3)
    pos = fb.positions()[0]
    assert pos["side"] == "short" and pos["qty"] == 5.0
    fb.prices["NVDA"] = 180.3
    assert fb.positions()[0]["unrealized_pl"] == 10.0
    fb.hit(order["id"], "target", 175.3)
    assert fb.positions() == []


def test_fake_oco_protects_an_existing_position():
    fb = FakeBroker()
    fb.set_position("SPY", "long", 10, 100.0)
    oco = fb.submit_oco("SPY", "sell", 10, stop=95.0, target=110.0)
    legs = exit_legs(oco)
    assert legs["target"]["id"] == oco["id"] and legs["stop"]["stop_price"] == 95.0
    assert len(fb.open_orders("SPY")) == 2
    fb.hit(oco["id"], "target", 110.0)
    assert fb.positions() == [] and fb.open_orders() == []


def test_fake_cancel_market_exit_and_failures():
    fb = FakeBroker()
    fb.prices["SPY"] = 101.0
    order = fb.submit_bracket("SPY", "buy", 10, stop=95.0, target=110.0)
    fb.fill(order["id"], 100.0)
    for o in fb.open_orders("SPY"):
        fb.cancel(o["id"])
    assert fb.open_orders("SPY") == []
    fb.auto_fill_market = True
    fb.submit_market("SPY", "sell", 10)
    assert fb.positions() == []
    fb.fail_next("positions", ConnectionError("blip"))
    with pytest.raises(ConnectionError):
        fb.positions()
    assert fb.positions() == []


# ---------------------------------------------------------------- normalisers

def _alpaca_order(**over):
    base = dict(id="abc", client_order_id="c1", symbol="SPY", side=OrderSide.BUY, qty="10",
                filled_qty="10", filled_avg_price="100.25", type=NS(value="market"),
                order_class=OrderClass.BRACKET, time_in_force=TimeInForce.GTC,
                status=NS(value="filled"), limit_price=None, stop_price=None,
                submitted_at=datetime(2026, 10, 30, 14, 15, tzinfo=ZoneInfo("UTC")), legs=None)
    base.update(over)
    return NS(**base)


def test_normalize_order_with_legs():
    legs = [_alpaca_order(id="tp", side=OrderSide.SELL, type=NS(value="limit"), limit_price="110",
                          order_class=OrderClass.SIMPLE, status=NS(value="new"), filled_qty="0",
                          filled_avg_price=None),
            _alpaca_order(id="sl", side=OrderSide.SELL, type=NS(value="stop"), stop_price="95",
                          order_class=OrderClass.SIMPLE, status=NS(value="new"), filled_qty="0",
                          filled_avg_price=None)]
    out = normalize_order(_alpaca_order(legs=legs))
    assert out["side"] == "buy" and out["order_class"] == "bracket" and out["tif"] == "gtc"
    assert out["qty"] == 10.0 and out["filled_avg"] == 100.25 and out["status"] == "filled"
    assert out["submitted_at"] == "2026-10-30T10:15:00-04:00"
    assert exit_legs(out)["stop"]["stop_price"] == 95.0
    assert exit_legs(out)["target"]["limit_price"] == 110.0


def test_normalize_short_position():
    pos = NS(symbol="NVDA", side=NS(value="short"), qty="-5", avg_entry_price="182.3",
             current_price="180.3", market_value="-901.5", unrealized_pl="10")
    out = normalize_position(pos)
    assert out == {"symbol": "NVDA", "side": "short", "qty": 5.0, "avg_entry": 182.3,
                   "current_price": 180.3, "market_value": -901.5, "unrealized_pl": 10.0}


# ---------------------------------------------------------------- adapter requests

class StubTrading:
    def __init__(self):
        self.requests = []

    def submit_order(self, req):
        self.requests.append(req)
        return _alpaca_order(status=NS(value="new"), filled_qty="0", filled_avg_price=None,
                             side=req.side, order_class=req.order_class or OrderClass.SIMPLE)

    def get_asset(self, symbol):
        return NS(symbol=symbol, tradable=True, shortable=True, easy_to_borrow=False)

    def get_account(self):
        return NS(equity="100000", cash="40000", buying_power="180000", shorting_enabled=True)


def test_bracket_request_long():
    trading = StubTrading()
    out = Broker(trading, None).submit_bracket("SPY", "buy", 10, stop=94.996, target=110.004)
    req = trading.requests[0]
    assert req.order_class == OrderClass.BRACKET and req.side == OrderSide.BUY
    assert req.time_in_force == TimeInForce.GTC and req.qty == 10
    assert req.stop_loss.stop_price == 95.0 and req.take_profit.limit_price == 110.0
    assert out["id"] == "abc"


def test_bracket_request_short_day():
    trading = StubTrading()
    Broker(trading, None).submit_bracket("NVDA", "sell", 5, stop=185.8, target=175.3, tif="day")
    req = trading.requests[0]
    assert req.side == OrderSide.SELL and req.time_in_force == TimeInForce.DAY


def test_oco_request():
    trading = StubTrading()
    Broker(trading, None).submit_oco("SPY", "sell", 10, stop=95.0, target=110.0)
    req = trading.requests[0]
    assert req.order_class == OrderClass.OCO and req.side == OrderSide.SELL
    assert req.stop_loss.stop_price == 95.0 and req.take_profit.limit_price == 110.0
    assert req.time_in_force == TimeInForce.GTC


def test_bad_side_and_qty_are_refused_before_alpaca():
    b = Broker(StubTrading(), None)
    with pytest.raises(ValueError):
        b.submit_bracket("SPY", "long", 10, stop=95.0, target=110.0)
    with pytest.raises(ValueError):
        b.submit_bracket("SPY", "buy", 0, stop=95.0, target=110.0)
    with pytest.raises(ValueError):
        b.submit_bracket("SPY", "buy", 1.5, stop=95.0, target=110.0)


def test_asset_and_account():
    b = Broker(StubTrading(), None)
    assert b.asset("GME") == {"symbol": "GME", "tradable": True, "shortable": True, "easy_to_borrow": False}
    assert b.account() == {"equity": 100_000.0, "cash": 40_000.0, "buying_power": 180_000.0,
                           "shorting_enabled": True}


def test_from_env_is_always_paper(monkeypatch):
    seen = {}

    class FakeTradingClient:
        def __init__(self, key, secret, paper):
            seen["paper"] = paper

    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(pbroker, "_trading_client_cls", lambda: FakeTradingClient)
    monkeypatch.setattr(pbroker, "_data_client", lambda key, secret: None)
    Broker.from_env()
    assert seen["paper"] is True


def test_from_env_without_keys(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.setattr(pbroker, "load_dotenv", lambda *a, **k: None)
    with pytest.raises(pbroker.BrokerError, match="ALPACA_API_KEY"):
        Broker.from_env()


def test_daily_bars_drop_the_bar_in_progress():
    idx = pd.to_datetime(["2026-10-28 04:00", "2026-10-29 04:00", "2026-10-30 04:00"], utc=True)
    raw = pd.DataFrame({"open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3], "close": [1, 2, 3],
                        "volume": [10, 20, 30]}, index=idx)
    raw.index = pd.MultiIndex.from_arrays([["SPY"] * 3, raw.index], names=["symbol", "timestamp"])
    out = pbroker.bars_frame(raw, "SPY", NOW)
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert [d.day for d in out.index] == [28, 29]


def test_filled_today_looks_at_todays_closed_orders():
    class Trading:
        def __init__(self):
            self.req = None

        def get_orders(self, filter):
            self.req = filter
            return [_alpaca_order(side=OrderSide.SELL, filled_qty="0"),
                    _alpaca_order(side=OrderSide.BUY, filled_qty="10")]

    t = Trading()
    b = Broker(t, None)
    assert b.filled_today("SPY", "buy", NOW) is True
    assert b.filled_today("SPY", "sell", NOW) is False
    assert t.req.after == NOW.replace(hour=0, minute=0) and t.req.symbols == ["SPY"]
