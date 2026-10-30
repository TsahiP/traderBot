"""paper.bot: whole days on a fake clock and a fake broker - the swing pass, queued
entries, fills from the stream, pause, manual approval, flat-by, config reloads,
restarts, heartbeat and the /state snapshot."""
import json
import os
from datetime import datetime

import pytest

from paper import config as pcfg
from paper.bot import Bot
from paper.broker import exit_legs
from paper.bus import EventBus
from paper.executor import Executor
from paper.journal import Journal
from paper_fakes import NY, FakeBroker, FakeQuotes, cross_bars, flat_bars

THU = datetime(2026, 10, 29, 16, 20, tzinfo=NY)          # after Thursday's close: swing pass
FRI = datetime(2026, 10, 30, 9, 45, tzinfo=NY)           # Friday's entry window opens


def at(day: datetime, hh: int, mm: int) -> datetime:
    return day.replace(hour=hh, minute=mm)


@pytest.fixture
def env(tmp_path):
    journal = Journal(tmp_path / "paperbot.db")
    bus = EventBus(journal)
    events = bus.subscribe(maxsize=10_000)
    fb = FakeBroker()
    fb.prices["SPY"] = 120.0
    fb.bars["SPY"] = cross_bars("up", "2026-10-29")
    cfg_path = tmp_path / "paperbot_config.json"
    pcfg.load_config(cfg_path)

    def make_bot():
        return Bot(fb, journal, bus, cfg_path, executor=Executor(fb, journal, bus, sleep=lambda s: None),
                   quotes=FakeQuotes(), pid=4242)

    yield make_bot(), fb, journal, events, make_bot, cfg_path
    journal.close()


def _drain(events):
    out = []
    while not events.empty():
        out.append(events.get_nowait())
    return out


def _types(events):
    return [e["type"] for e in _drain(events)]


def _update(fb, order_id, event="fill", position_qty=None):
    order = dict(fb.order(order_id))
    return {"kind": "trade_update", "event": event, "order": order, "price": order["filled_avg"],
            "qty": order["filled_qty"], "position_qty": position_qty, "timestamp": "t", "execution_id": None}


def _command(bot, journal, kind, **args):
    cid = journal.add_command("ui", kind, args)
    bot.post({"kind": "command", "id": cid, "cmd": kind, "args": args})
    return cid


def _brackets(fb):
    return [c for c in fb.calls if c[0] == "submit_bracket"]


def _setup(journal, day="2026-10-29"):
    return journal.get_setup("swing_sma", "SPY", day)


# ---------------------------------------------------------------- the swing day

def test_cross_after_the_close_is_queued_then_sent_filled_and_stopped(env):
    bot, fb, journal, events, _, _ = env
    bot.tick(THU)
    setup = _setup(journal)
    assert setup["state"] == "TRIGGERED" and setup["detail"]["queued"]["entry_at"].startswith("2026-10-30T09:45")
    assert "queued for Fri 09:45" in setup["sentence"]
    assert _brackets(fb) == []
    assert "swing.pass" in _types(events)

    bot.tick(at(FRI, 9, 40))
    assert _brackets(fb) == []
    bot.tick(FRI)
    [call] = _brackets(fb)
    assert call[1:3] == ("SPY", "buy")
    assert _setup(journal)["state"] == "ENTERED"
    entry = next(o for o in journal.orders() if o["kind"] == "entry")
    decision = journal.decision(entry["decision_id"])
    assert decision["outcome"] == "entered"

    fb.fill(entry["alpaca_id"], 120.1)
    bot.post(_update(fb, entry["alpaca_id"]))
    bot.tick(at(FRI, 9, 46))
    [trade] = journal.open_trades()
    assert (trade["run_id"], trade["side"], trade["entry_price"]) == ("swing_sma", "long", 120.1)
    assert (trade["stop"], trade["target"]) == (decision["plan"]["stop"], decision["plan"]["target"])
    assert trade["flat_by"] is None                    # swing: GTC, no forced exit
    assert _setup(journal)["state"] == "MANAGING"
    assert "position.opened" in _types(events)

    stop_leg = exit_legs(fb.order(entry["alpaca_id"]))["stop"]
    fb.hit(entry["alpaca_id"], "stop", trade["stop"])
    bot.post(_update(fb, stop_leg["id"], position_qty=0))
    bot.tick(at(FRI, 11, 0))
    [closed] = journal.trades(closed=True)
    assert closed["exit_reason"] == "stop" and closed["r"] == pytest.approx(-1.0, abs=0.05)
    assert _setup(journal)["state"] == "EXITED"
    assert "trade.closed" in _types(events)


def test_a_restart_does_not_rerun_the_pass_and_still_sends_the_queue(env):
    bot, fb, journal, _, make_bot, _ = env
    bot.tick(THU)
    again = make_bot()
    again.tick(at(THU, 16, 30))
    assert len([e for e in journal.events_since(0, 1000) if e["type"] == "swing.pass"]) == 1
    again.tick(FRI)
    assert len(_brackets(fb)) == 1


def test_a_queued_entry_never_fires_late(env):
    bot, fb, journal, _, _, _ = env
    bot.tick(THU)
    bot.tick(datetime(2026, 11, 2, 10, 0, tzinfo=NY))        # the bot slept through Friday
    assert _setup(journal)["state"] == "EXPIRED" and _brackets(fb) == []


def test_exit_signal_at_the_pass_sells_and_the_fill_closes_the_trade(env):
    bot, fb, journal, events, _, _ = env
    fb.bars["SPY"] = cross_bars("down", "2026-10-29")
    fb.set_position("SPY", "long", 10, 100.0)
    tid = journal.open_trade(run_id="swing_sma", symbol="SPY", side="long", qty=10, entry_ts="2026-10-01",
                             entry_price=100.0, stop=95.0, target=110.0)
    bot.tick(THU)
    sell = next(c for c in fb.calls if c[0] == "submit_market")
    assert sell[1:3] == ("SPY", "sell")
    order = next(o for o in journal.orders() if o["kind"] == "signal")
    fb.fill(order["alpaca_id"], 98.0)
    bot.post(_update(fb, order["alpaca_id"], position_qty=0))
    bot.tick(at(FRI, 9, 31))
    closed = journal.trade(tid)
    assert closed["exit_reason"] == "signal" and closed["exit_price"] == 98.0 and closed["r"] == -0.4


# ---------------------------------------------------------------- pause, approval, commands

def test_pause_blocks_entries_but_not_exits(env):
    bot, fb, journal, events, _, _ = env
    bot.tick(THU)
    _command(bot, journal, "pause")
    bot.tick(at(FRI, 9, 0))
    assert bot.paused and journal.read_heartbeat()["paused"] is True
    bot.tick(FRI)
    assert _setup(journal)["state"] == "BLOCKED" and "paused" in _setup(journal)["sentence"]
    fb.set_position("QQQ", "long", 2, 400.0)
    cid = _command(bot, journal, "flatten_all")
    bot.tick(at(FRI, 10, 0))
    assert journal.command(cid)["status"] == "done"
    assert ("submit_market", "QQQ", "sell", 2.0, "day") in fb.calls
    _command(bot, journal, "resume")
    bot.tick(at(FRI, 10, 1))
    assert not bot.paused and {"bot.paused", "bot.resumed"} <= set(_types(events))


def test_manual_approval_from_proposal_to_order(env):
    bot, fb, journal, events, _, cfg_path = env
    _command(bot, journal, "set_approval", mode="manual")
    bot.tick(at(THU, 16, 0))
    assert json.loads(cfg_path.read_text())["approval_mode"] == "manual"
    assert "bot.config" in _types(events)
    bot.tick(THU)
    setup = _setup(journal)
    pid = setup["detail"]["proposal_id"]
    assert setup["state"] == "PROPOSED" and _brackets(fb) == []

    early = _command(bot, journal, "approve", proposal_id=pid)
    bot.tick(at(THU, 16, 30))
    assert journal.command(early)["status"] == "error"
    assert "entry window" in journal.command(early)["result"]["error"]

    bot.tick(FRI)
    assert _brackets(fb) == []                          # a proposal never sends itself
    ok = _command(bot, journal, "approve", proposal_id=pid)
    bot.tick(at(FRI, 9, 50))
    assert journal.command(ok)["status"] == "done"
    assert len(_brackets(fb)) == 1 and _setup(journal)["state"] == "ENTERED"


def test_an_unapproved_proposal_expires_at_the_window_end(env):
    bot, fb, journal, _, _, _ = env
    _command(bot, journal, "set_approval", mode="manual")
    bot.tick(THU)
    bot.tick(at(FRI, 15, 31))
    assert _setup(journal)["state"] == "EXPIRED" and _brackets(fb) == []
    assert bot.executor.pending_proposals() == []


def test_switching_approval_off_leaves_a_pending_proposal_alone(env):
    bot, fb, journal, _, _, _ = env
    _command(bot, journal, "set_approval", mode="manual")
    bot.tick(THU)
    _command(bot, journal, "set_approval", mode="auto")
    bot.tick(at(FRI, 9, 50))
    assert bot.cfg["approval_mode"] == "auto"
    assert len(bot.executor.pending_proposals()) == 1 and _brackets(fb) == []


def test_reject_and_bad_commands(env):
    bot, fb, journal, _, _, _ = env
    _command(bot, journal, "set_approval", mode="manual")
    bot.tick(THU)
    pid = _setup(journal)["detail"]["proposal_id"]
    rej = _command(bot, journal, "reject", proposal_id=pid)
    nope = _command(bot, journal, "close_position", symbol="TSLA")
    weird = _command(bot, journal, "launch_rocket")
    bot.tick(at(THU, 16, 40))
    assert journal.command(rej)["status"] == "done" and _setup(journal)["state"] == "BLOCKED"
    assert journal.command(nope)["status"] == "error" and "TSLA" in journal.command(nope)["result"]["error"]
    assert journal.command(weird)["status"] == "error"


def test_an_entry_order_that_dies_unfilled(env):
    bot, fb, journal, events, _, _ = env
    bot.tick(THU)
    bot.tick(FRI)
    entry = next(o for o in journal.orders() if o["kind"] == "entry")
    fb.cancel(entry["alpaca_id"])
    bot.post(_update(fb, entry["alpaca_id"], event="canceled"))
    bot.tick(at(FRI, 9, 47))
    assert journal.decision(entry["decision_id"])["outcome"] == "expired"
    assert _setup(journal)["state"] == "BLOCKED"
    assert "order.canceled" in _types(events)


# ---------------------------------------------------------------- flat-by, quotes, config, calendar

def test_flat_by_exits_once(env):
    bot, fb, journal, _, _, _ = env
    fb.set_position("SPY", "long", 10, 100.0)
    journal.open_trade(run_id="adopted", symbol="SPY", side="long", qty=10, entry_ts="t", entry_price=100.0,
                       stop=96.0, target=108.0, adopted=True, flat_by="15:55")
    bot.tick(at(FRI, 15, 54))
    assert not [c for c in fb.calls if c[0] == "submit_market"]
    bot.tick(at(FRI, 15, 55))
    bot.tick(at(FRI, 15, 56))
    assert len([c for c in fb.calls if c[0] == "submit_market"]) == 1
    assert next(o for o in journal.orders() if o["kind"] == "flat_by")


def test_quotes_track_mae_mfe_and_the_watched_symbols(env):
    bot, fb, journal, _, _, _ = env
    fb.set_position("SPY", "long", 10, 100.0)
    tid = journal.open_trade(run_id="swing_sma", symbol="SPY", side="long", qty=10, entry_ts="t",
                             entry_price=100.0, stop=96.0, target=108.0)
    for price in (99.0, 103.0, 101.0):
        bot.post({"kind": "quote", "symbol": "SPY", "price": price, "size": 1.0, "timestamp": "t"})
    bot.tick(at(FRI, 10, 0))
    t = journal.trade(tid)
    assert (t["mae_r"], t["mfe_r"]) == (-0.25, 0.75)
    assert bot.quotes.symbols == {"SPY"} and bot.prices["SPY"] == 101.0
    pos = bot.snapshot["positions"][0]
    assert pos["price"] == 101.0 and pos["pnl"] == 10.0 and pos["r"] == 0.25
    assert pos["trade"]["run_id"] == "swing_sma"


def test_config_edits_apply_and_a_broken_file_is_survived(env):
    bot, _, _, events, _, cfg_path = env
    before = bot.cfg_hash
    cfg = json.loads(cfg_path.read_text())
    cfg["llm_mode"] = "off"
    cfg_path.write_text(json.dumps(cfg))
    os.utime(cfg_path, (1, 1))
    bot.tick(at(FRI, 10, 0))
    assert bot.cfg["llm_mode"] == "off" and bot.cfg_hash != before
    cfg_path.write_text("{oops")
    os.utime(cfg_path, (2, 2))
    _drain(events)
    bot.tick(at(FRI, 10, 1))
    assert bot.cfg["llm_mode"] == "off"
    assert any(e["type"] == "error" and "config" in e["payload"]["message"] for e in _drain(events))


def test_holidays_do_nothing_but_the_heartbeat(env):
    bot, fb, journal, _, _, _ = env
    thanksgiving = datetime(2026, 11, 26, 16, 30, tzinfo=NY)
    bot.tick(thanksgiving)
    assert journal.last_event("swing.pass") is None
    hb = journal.read_heartbeat()
    assert hb["market"] == "closed" and hb["pid"] == 4242 and hb["config_hash"] == bot.cfg_hash


def test_start_reconciles_and_snapshot_shape(env):
    bot, fb, journal, events, _, _ = env
    fb.bars["QQQ"] = flat_bars()
    fb.prices["QQQ"] = 100.0
    fb.set_position("QQQ", "long", 3, 100.0)
    bot.start(at(FRI, 10, 0))
    assert journal.open_trades()[0]["adopted"] is True
    types = _types(events)
    assert "bot.start" in types and "position.adopted" in types
    snap = bot.snapshot
    assert set(snap) >= {"ts", "paused", "market", "streams", "config", "account", "positions", "proposals",
                         "setups", "counters", "last_event_id"}
    assert snap["market"] == "open" and snap["config"]["approval_mode"] == "auto"
    assert snap["counters"]["open_risk_usd"] == 12.0          # 3 x (100 - 96)
    assert bot.next_wake.startswith("2026-10-30T16:20")
