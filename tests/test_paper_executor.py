"""paper.executor: entries re-checked at send time and sent as brackets,
proposals (approve / reject / expire), exits that cancel the legs first."""
from datetime import datetime, timedelta

import pytest

from paper import config as pcfg
from paper.broker import exit_legs
from paper.bus import EventBus
from paper.executor import Executor, ExecutorError
from paper.funnel import run_funnel
from paper.journal import Journal
from paper_fakes import ENTRY_AT, NY, FakeBroker, entry_intent, funnel_ctx, swing_run

HASH = "abc"


@pytest.fixture
def env(tmp_path):
    journal = Journal(tmp_path / "paperbot.db")
    bus = EventBus(journal)
    events = bus.subscribe()
    fb = FakeBroker()
    fb.prices["SPY"] = 100.0
    ex = Executor(fb, journal, bus, sleep=lambda s: None)
    yield ex, fb, journal, events
    journal.close()


def _types(events):
    out = []
    while not events.empty():
        out.append(events.get_nowait()["type"])
    return out


def _manual_cfg():
    cfg = pcfg.default_config()
    cfg["approval_mode"] = "manual"
    return cfg


def _propose(ex, ctx=None, expires_in=timedelta(days=1)):
    ctx = ctx or funnel_ctx(cfg=_manual_cfg())
    decision = run_funnel(entry_intent(), ctx)
    assert decision.outcome == "proposed"
    return ex.propose(entry_intent(), decision, now=ctx.now_ny, config_hash=HASH,
                      expires_at=ctx.now_ny + expires_in)


# ---------------------------------------------------------------- send

def test_send_places_a_gtc_bracket_and_journals_it(env):
    ex, fb, journal, events = env
    res = ex.send(entry_intent(), funnel_ctx(), now=ENTRY_AT, config_hash=HASH)
    assert res.ok and res.decision.outcome == "ready"
    order = res.order
    assert ("submit_bracket", "SPY", "buy", 50, 96.0, 108.0, "gtc") in fb.calls
    row = journal.decision(res.decision_id)
    assert row["outcome"] == "entered" and row["qty"] == 50 and row["max_loss"] == 200.0
    assert row["plan"]["intent"]["reason"].startswith("SMA 10")
    assert journal.entries_today("2026-10-30") == [{"symbol": "SPY", "max_loss": 200.0}]
    kinds = {o["kind"] for o in journal.orders()}
    assert kinds == {"entry", "stop", "target"}
    assert journal.order(order["id"])["decision_id"] == res.decision_id
    assert _types(events) == ["decision", "order.submitted"]


def test_send_a_short_and_a_day_run(env):
    ex, fb, _, _ = env
    ex.send(entry_intent("short"), funnel_ctx(run=swing_run(allow_short=True)), now=ENTRY_AT,
            config_hash=HASH)
    assert ("submit_bracket", "SPY", "sell", 50, 104.0, 92.0, "gtc") in fb.calls


def test_send_rechecks_and_records_a_block(env):
    ex, fb, journal, events = env
    held = [{"symbol": "SPY", "side": "long", "qty": 5.0, "avg_entry": 99.0}]
    res = ex.send(entry_intent(), funnel_ctx(positions=held), now=ENTRY_AT, config_hash=HASH)
    assert not res.ok and res.order is None
    assert journal.decision(res.decision_id)["outcome"] == "blocked"
    assert not [c for c in fb.calls if c[0] == "submit_bracket"]
    assert _types(events) == ["decision"]


def test_send_records_a_broker_rejection(env):
    ex, fb, journal, events = env
    fb.fail_next("submit_bracket", RuntimeError("insufficient buying power"))
    res = ex.send(entry_intent(), funnel_ctx(), now=ENTRY_AT, config_hash=HASH)
    assert not res.ok and "insufficient" in res.error
    row = journal.decision(res.decision_id)
    assert row["outcome"] == "rejected" and row["failed_stage"] == "execute"
    assert journal.orders() == [] and journal.entries_today("2026-10-30") == []
    assert _types(events) == ["decision", "error"]


def test_send_always_sends_even_in_manual_mode(env):
    ex, _, _, _ = env
    # manual mode is the caller's choice to propose(); send() means "go"
    res = ex.send(entry_intent(), funnel_ctx(cfg=_manual_cfg()), now=ENTRY_AT, config_hash=HASH)
    assert res.ok


# ---------------------------------------------------------------- proposals

def test_propose_stores_a_pending_proposal(env):
    ex, _, journal, events = env
    p = _propose(ex)
    assert p["outcome"] == "proposed" and p["plan"]["proposal"]["trigger_price"] == 100.0
    assert [x["id"] for x in ex.pending_proposals()] == [p["id"]]
    assert journal.entries_today("2026-10-30") == []        # not an entry until sent
    assert _types(events) == ["proposal.created"]


def test_approve_resizes_at_the_new_price_and_sends(env):
    ex, fb, journal, events = env
    p = _propose(ex)
    _types(events)
    fresh = funnel_ctx(price=101.0, pending_symbols={"SPY"})   # its own proposal is pending
    res = ex.approve(p["id"], fresh, now=ENTRY_AT, config_hash=HASH)
    assert res.ok
    assert ("submit_bracket", "SPY", "buy", 50, 97.0, 109.0, "gtc") in fb.calls
    assert journal.decision(p["id"])["outcome"] == "entered"
    assert ex.pending_proposals() == []
    assert _types(events) == ["proposal.approved", "order.submitted"]


def test_approve_blocks_when_the_recheck_fails(env):
    ex, fb, journal, events = env
    p = _propose(ex)
    _types(events)
    used = [{"symbol": s, "max_loss": 100.0} for s in "ABCDEF"]
    res = ex.approve(p["id"], funnel_ctx(entries_today=used), now=ENTRY_AT, config_hash=HASH)
    assert not res.ok and "global trades" in res.error
    row = journal.decision(p["id"])
    assert row["outcome"] == "blocked" and row["failed_stage"] == "risk"
    assert not [c for c in fb.calls if c[0] == "submit_bracket"]
    assert _types(events) == ["proposal.approved", "decision"]


def test_approve_and_reject_need_a_pending_proposal(env):
    ex, _, journal, events = env
    p = _propose(ex)
    ex.reject(p["id"], now=ENTRY_AT)
    assert journal.decision(p["id"])["outcome"] == "rejected"
    with pytest.raises(ExecutorError):
        ex.approve(p["id"], funnel_ctx(), now=ENTRY_AT, config_hash=HASH)
    with pytest.raises(ExecutorError):
        ex.reject(p["id"], now=ENTRY_AT)
    with pytest.raises(ExecutorError):
        ex.reject(999, now=ENTRY_AT)


def test_proposals_expire_by_time_and_by_drift(env):
    ex, _, journal, events = env
    timed = _propose(ex, expires_in=timedelta(hours=1))
    start = funnel_ctx().now_ny
    assert ex.expire_due(start + timedelta(minutes=30), {"SPY": 100.2}, max_drift_pct=0.3) == []
    expired = ex.expire_due(start + timedelta(hours=2), {"SPY": 100.0}, max_drift_pct=0.3)
    assert [e["id"] for e in expired] == [timed["id"]] and "timed out" in expired[0]["reason"]
    drift = _propose(ex)
    out = ex.expire_due(start + timedelta(minutes=5), {"SPY": 100.5}, max_drift_pct=0.3)
    assert [e["id"] for e in out] == [drift["id"]] and "moved 0.50%" in out[0]["reason"]
    assert journal.decision(drift["id"])["outcome"] == "expired"
    assert "proposal.expired" in _types(events)


# ---------------------------------------------------------------- exits

def test_exit_cancels_the_legs_then_sells(env):
    ex, fb, journal, events = env
    res = ex.send(entry_intent(), funnel_ctx(), now=ENTRY_AT, config_hash=HASH)
    fb.fill(res.order["id"], 100.0)
    _types(events)
    out = ex.exit_position("SPY", "signal", now=ENTRY_AT)
    names = [c[0] for c in fb.calls]
    assert names.index("cancel") < names.index("submit_market")
    assert ("submit_market", "SPY", "sell", 50.0, "day") in fb.calls
    assert all(leg["status"] == "canceled" for leg in exit_legs(fb.order(res.order["id"])).values())
    assert journal.order(out["id"])["kind"] == "signal"
    assert _types(events) == ["order.submitted"]


def test_exit_a_short_buys_to_cover(env):
    ex, fb, _, _ = env
    fb.set_position("NVDA", "short", 5, 182.3)
    ex.exit_position("NVDA", "manual", now=ENTRY_AT)
    assert ("submit_market", "NVDA", "buy", 5.0, "day") in fb.calls


def test_flatten_all_and_close_position(env):
    ex, fb, _, _ = env
    fb.auto_fill_market = True
    fb.prices.update({"SPY": 100.0, "QQQ": 400.0, "NVDA": 180.0})
    fb.set_position("SPY", "long", 10, 99.0)
    fb.set_position("QQQ", "long", 2, 390.0)       # adopted: the bot never opened it
    fb.set_position("NVDA", "short", 5, 182.3)
    orders = ex.flatten_all(now=ENTRY_AT)
    assert len(orders) == 3 and fb.positions() == []
    with pytest.raises(ExecutorError):
        ex.close_position("SPY", now=ENTRY_AT)


def test_exit_still_sends_when_cancels_hang(env):
    ex, fb, _, _ = env
    fb.set_position("SPY", "long", 10, 99.0)
    oco = fb.submit_oco("SPY", "sell", 10, stop=95.0, target=110.0)
    fb.cancel = lambda oid: fb.calls.append(("cancel", oid))      # never actually cancels
    ex.exit_position("SPY", "manual", now=ENTRY_AT)
    assert ("submit_market", "SPY", "sell", 10.0, "day") in fb.calls
    assert oco["status"] == "new"
