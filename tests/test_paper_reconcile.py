"""paper.reconcile: line the journal up with Alpaca at startup (spec §5.8)."""
import pytest

from paper.bus import EventBus
from paper.executor import Executor
from paper.journal import Journal
from paper.reconcile import reconcile
from paper_fakes import ENTRY_AT, FakeBroker, entry_intent, flat_bars, funnel_ctx

HASH = "abc"


@pytest.fixture
def env(tmp_path):
    journal = Journal(tmp_path / "paperbot.db")
    bus = EventBus(journal)
    events = bus.subscribe()
    fb = FakeBroker()
    fb.prices.update({"SPY": 100.0, "QQQ": 400.0, "NVDA": 100.0})
    for sym in ("SPY", "QQQ", "NVDA"):
        fb.bars[sym] = flat_bars()
    ex = Executor(fb, journal, bus, sleep=lambda s: None)
    yield fb, journal, bus, events, ex
    journal.close()


def _drain(events):
    out = []
    while not events.empty():
        out.append(events.get_nowait())
    return out


def _types(events):
    return [e["type"] for e in _drain(events)]


def _adopted_event(events):
    return next(e for e in _drain(events) if e["type"] == "position.adopted")


def _run(fb, journal, bus):
    return reconcile(fb, journal, bus, now=ENTRY_AT, flat_by="15:55")


def _sent_and_filled(fb, ex, price=100.0):
    res = ex.send(entry_intent(), funnel_ctx(), now=ENTRY_AT, config_hash=HASH)
    fb.fill(res.order["id"], price)
    return res


def test_an_entry_that_filled_while_offline_opens_its_trade(env):
    fb, journal, bus, events, ex = env
    res = _sent_and_filled(fb, ex, 100.1)
    _types(events)
    report = _run(fb, journal, bus)
    [trade] = journal.open_trades()
    assert report.opened == [trade["id"]]
    assert (trade["run_id"], trade["side"], trade["qty"], trade["entry_price"]) == ("swing_sma", "long", 50.0, 100.1)
    assert (trade["stop"], trade["target"], trade["decision_id"]) == (96.0, 108.0, res.decision_id)
    assert trade["adopted"] is False and trade["config_hash"] == HASH
    assert journal.order(res.order["id"])["status"] == "filled"
    assert "position.opened" in _types(events)


def test_stop_filled_while_offline_closes_the_trade(env):
    fb, journal, bus, events, ex = env
    res = _sent_and_filled(fb, ex)
    _run(fb, journal, bus)
    fb.hit(res.order["id"], "stop", 95.9)
    _types(events)
    report = _run(fb, journal, bus)
    [closed] = journal.trades(closed=True)
    assert report.closed == [closed["id"]]
    assert closed["exit_reason"] == "stop" and closed["exit_price"] == 95.9
    assert closed["r"] == pytest.approx(-1.025)
    assert _types(events) == ["trade.closed"]


def test_a_market_exit_while_offline_keeps_its_reason(env):
    fb, journal, bus, _, ex = env
    _sent_and_filled(fb, ex)
    _run(fb, journal, bus)
    order = ex.exit_position("SPY", "signal", now=ENTRY_AT)
    fb.fill(order["id"], 103.0)
    _run(fb, journal, bus)
    [closed] = journal.trades(closed=True)
    assert closed["exit_reason"] == "signal" and closed["exit_price"] == 103.0


def test_closed_with_no_trace_is_recorded_at_the_last_price(env):
    fb, journal, bus, _, _ = env
    tid = journal.open_trade(run_id="adopted", symbol="QQQ", side="long", qty=2, entry_ts="t",
                             entry_price=390.0, stop=None, target=None, adopted=True)
    _run(fb, journal, bus)
    closed = journal.trade(tid)
    assert closed["exit_reason"] == "broker" and closed["exit_price"] == 400.0 and closed["pnl"] == 20.0


def test_a_pending_entry_is_reattached(env):
    fb, journal, bus, _, ex = env
    res = ex.send(entry_intent(), funnel_ctx(), now=ENTRY_AT, config_hash=HASH)
    report = _run(fb, journal, bus)
    assert report.pending == ["SPY"] and journal.open_trades() == []
    assert not [c for c in fb.calls if c[0] == "submit_oco"]
    assert journal.order(res.order["id"])["status"] == "new"


def test_an_entry_that_died_unfilled_is_closed_out(env):
    fb, journal, bus, events, ex = env
    res = ex.send(entry_intent(), funnel_ctx(), now=ENTRY_AT, config_hash=HASH)
    fb.cancel(res.order["id"])
    _types(events)
    report = _run(fb, journal, bus)
    assert report.pending == [] and journal.order(res.order["id"])["status"] == "canceled"
    assert "order.canceled" in _types(events)


def test_untracked_position_with_its_own_stop_and_target_is_adopted_as_is(env):
    fb, journal, bus, events, _ = env
    fb.set_position("SPY", "long", 10, 100.0)
    oco = fb.submit_oco("SPY", "sell", 10, stop=94.5, target=111.0)
    calls_before = len([c for c in fb.calls if c[0] == "submit_oco"])
    report = _run(fb, journal, bus)
    [trade] = journal.open_trades()
    assert report.adopted == [trade["id"]] and trade["adopted"] is True and trade["run_id"] == "adopted"
    assert (trade["stop"], trade["target"]) == (94.5, 111.0)
    assert len([c for c in fb.calls if c[0] == "submit_oco"]) == calls_before
    assert {o["kind"] for o in journal.orders_matching(trade_id=trade["id"])} == {"stop", "target"}
    assert _adopted_event(events)["payload"]["protection"] == "existing"
    assert oco["status"] == "new"


def test_naked_untracked_position_gets_a_protective_oco(env):
    fb, journal, bus, _, _ = env
    fb.set_position("SPY", "long", 10, 100.0)
    _run(fb, journal, bus)
    assert ("submit_oco", "SPY", "sell", 10.0, 96.0, 108.0) in fb.calls
    [trade] = journal.open_trades()
    assert (trade["stop"], trade["target"], trade["flat_by"]) == (96.0, 108.0, None)
    [order] = journal.orders_matching(trade_id=trade["id"])
    assert order["kind"] == "protective"


def test_price_already_past_the_planned_stop(env):
    fb, journal, bus, events, _ = env
    fb.set_position("SPY", "long", 10, 100.0)
    fb.prices["SPY"] = 95.0                       # under avg - 2 x ATR = 96
    _run(fb, journal, bus)
    assert ("submit_oco", "SPY", "sell", 10.0, 93.0, 108.0) in fb.calls
    assert _adopted_event(events)["payload"]["protective_stop_moved"] is True


def test_naked_short_is_mirrored(env):
    fb, journal, bus, _, _ = env
    fb.set_position("NVDA", "short", 5, 100.0)
    _run(fb, journal, bus)
    assert ("submit_oco", "NVDA", "buy", 5.0, 104.0, 92.0) in fb.calls
    assert journal.open_trades()[0]["side"] == "short"


def test_opened_today_is_flattened_at_flat_by(env):
    fb, journal, bus, _, _ = env
    fb.set_position("SPY", "long", 10, 100.0)
    fb.set_position("NVDA", "short", 5, 100.0)
    fb.opened_today.add(("SPY", "buy"))
    _run(fb, journal, bus)
    flat = {t["symbol"]: t["flat_by"] for t in journal.open_trades()}
    assert flat == {"SPY": "15:55", "NVDA": None}


def test_no_bars_means_adopted_unprotected_with_an_error(env):
    fb, journal, bus, events, _ = env
    fb.set_position("QQQ", "long", 2, 390.0)
    fb.bars["QQQ"] = flat_bars(n=5)
    report = _run(fb, journal, bus)
    [trade] = journal.open_trades()
    assert trade["stop"] is None and report.errors
    assert not [c for c in fb.calls if c[0] == "submit_oco"]
    assert "error" in _types(events)


def test_reconcile_twice_changes_nothing(env):
    fb, journal, bus, _, _ = env
    fb.set_position("SPY", "long", 10, 100.0)
    _run(fb, journal, bus)
    report = _run(fb, journal, bus)
    assert report.adopted == [] and report.opened == [] and report.closed == []
    assert len(journal.open_trades()) == 1
    assert len([c for c in fb.calls if c[0] == "submit_oco"]) == 1


def test_trade_qty_follows_the_broker(env):
    fb, journal, bus, _, ex = env
    _sent_and_filled(fb, ex)
    _run(fb, journal, bus)
    fb.set_position("SPY", "long", 30, 100.0)      # partially closed outside the bot
    _run(fb, journal, bus)
    assert journal.open_trades()[0]["qty"] == 30.0
