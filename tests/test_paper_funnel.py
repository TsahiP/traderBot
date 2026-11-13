"""paper.funnel: every stage passes or blocks with a reason; the first block stops
the funnel; manual approval makes a proposal."""
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from paper import config as pcfg
from paper.funnel import STAGES, FunnelContext, run_funnel
from paper.runs import Intent, Run

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 29, 16, 5, tzinfo=NY)          # swing pass after the close
ENTRY_AT = datetime(2026, 10, 30, 9, 45, tzinfo=NY)     # the order goes out next morning


def _bars(n=60, rng=2.0, close=100.0):
    idx = pd.date_range("2026-08-01", periods=n, freq="B", tz=NY)
    c = np.full(n, close)
    return pd.DataFrame({"Open": c, "High": c + rng / 2, "Low": c - rng / 2, "Close": c,
                         "Volume": np.full(n, 1e6)}, index=idx)


def _run(**over) -> Run:
    raw = next(r for r in pcfg.default_config()["runs"] if r["id"] == "swing_sma")
    return Run.from_config({**raw, **over})


def _intent(side="long", symbol="SPY") -> Intent:
    return Intent("entry", "swing_sma", symbol, side, "2026-10-29", 100.0, "SMA 10 crossed above SMA 50")


def _ctx(**over) -> FunnelContext:
    base = dict(
        now_ny=NOW, entry_at=ENTRY_AT, cfg=pcfg.default_config(), run=_run(),
        universe={"SPY"}, account={"equity": 100_000.0, "cash": 100_000.0, "buying_power": 200_000.0},
        positions=[], pending_symbols=set(), entries_today=[], run_entries_today=[],
        open_risk_usd=0.0, short_exposure_usd=0.0,
        asset={"symbol": "SPY", "tradable": True, "shortable": True, "easy_to_borrow": True},
        bars=_bars(), price=100.0,
    )
    base.update(over)
    return FunnelContext(**base)


def _stage(decision, name):
    return next(s for s in decision.stages if s["stage"] == name)


def test_happy_path_is_ready_and_sized_by_max_loss():
    d = run_funnel(_intent(), _ctx())
    assert d.outcome == "ready" and d.failed_stage is None
    assert [s["stage"] for s in d.stages] == list(STAGES)
    assert {s["stage"]: s["status"] for s in d.stages} == {
        "universe": "pass", "position": "pass", "news": "skip", "trigger": "pass", "score": "skip",
        "exit_plan": "pass", "timemachine": "skip", "llm": "skip", "asset": "pass", "size": "pass",
        "risk": "pass", "approval": "pass"}
    assert d.plan["stop"] == 96.0 and d.plan["target"] == 108.0     # 2 x ATR 2, 2R
    assert (d.qty, d.limited_by, d.max_loss) == (50, "max_loss", 200.0)
    assert all(isinstance(s["ms"], float) for s in d.stages)
    assert d.llm_mode == "shadow" and d.llm_verdict is None


def test_first_fail_stops_the_funnel():
    d = run_funnel(_intent(symbol="QQQ"), _ctx())
    assert d.outcome == "blocked" and d.failed_stage == "universe"
    assert "QQQ" in _stage(d, "universe")["reason"]
    after = d.stages[1:]
    assert all(s["status"] == "skip" and s["reason"] == "not reached" for s in after)
    assert d.qty is None and d.plan is None


def test_one_position_per_symbol_across_runs():
    held = [{"symbol": "SPY", "side": "long", "qty": 10.0, "avg_entry": 99.0}]
    d = run_funnel(_intent(), _ctx(positions=held))
    assert d.failed_stage == "position" and "holding long 10" in _stage(d, "position")["reason"]
    d = run_funnel(_intent(), _ctx(pending_symbols={"SPY"}))
    assert d.failed_stage == "position" and "pending" in _stage(d, "position")["reason"]


def test_exit_plan_refusal_blocks():
    d = run_funnel(_intent(), _ctx(bars=_bars(n=5)))
    assert d.failed_stage == "exit_plan" and "ATR" in _stage(d, "exit_plan")["reason"]


def test_stages_not_built_yet_say_so():
    d = run_funnel(_intent(), _ctx())
    assert "phase" in _stage(d, "news")["reason"]
    assert "phase" in _stage(d, "score")["reason"]
    assert _stage(d, "timemachine")["reason"] == "off for this run"
    cfg = pcfg.default_config()
    cfg["llm_mode"] = "off"
    d = run_funnel(_intent(), _ctx(cfg=cfg))
    assert _stage(d, "llm")["reason"] == "LLM is off" and d.llm_mode == "off"


def test_assets_shorts_need_easy_to_borrow():
    run = _run(allow_short=True)
    htb = {"symbol": "SPY", "tradable": True, "shortable": True, "easy_to_borrow": False}
    d = run_funnel(_intent("short"), _ctx(run=run, asset=htb))
    assert d.failed_stage == "asset" and "easy to borrow" in _stage(d, "asset")["reason"]
    assert run_funnel(_intent("long"), _ctx(asset=htb)).outcome == "ready"
    dead = {"symbol": "SPY", "tradable": False, "shortable": False, "easy_to_borrow": False}
    assert run_funnel(_intent(), _ctx(asset=dead)).failed_stage == "asset"


def test_a_short_entry_is_sized_on_buying_power():
    run = _run(allow_short=True)
    d = run_funnel(_intent("short"), _ctx(run=run, account={"equity": 100_000.0, "cash": 0.0,
                                                              "buying_power": 200_000.0}))
    assert d.outcome == "ready" and d.plan["stop"] == 104.0 and d.qty == 50


def test_short_needs_the_run_to_allow_it():
    d = run_funnel(_intent("short"), _ctx())
    assert d.failed_stage == "trigger" and "short" in _stage(d, "trigger")["reason"]


def test_zero_shares_block_at_size():
    d = run_funnel(_intent(), _ctx(account={"equity": 1_000.0, "cash": 50.0, "buying_power": 50.0}))
    assert d.failed_stage == "size" and "0 shares" in _stage(d, "size")["reason"]


def test_run_daily_loss_blocks_at_risk():
    run = _run(risk={"max_loss_per_trade_usd": 200.0, "max_daily_loss_usd": 300.0})
    used = [{"symbol": "QQQ", "max_loss": 150.0}]
    d = run_funnel(_intent(), _ctx(run=run, run_entries_today=used, entries_today=used))
    risk_stage = _stage(d, "risk")
    assert d.failed_stage == "risk" and "run daily_loss" in risk_stage["reason"]
    assert risk_stage["value"]["run"] and not risk_stage["value"]["global"]


@pytest.mark.parametrize("over,needle", [
    (dict(positions=[{"symbol": s, "side": "long", "qty": 1.0, "avg_entry": 1.0}
                     for s in ("A", "B", "C", "D", "E")]), "global open_positions"),
    (dict(open_risk_usd=500.0), "global open_risk"),
    (dict(entries_today=[{"symbol": s, "max_loss": 1.0} for s in "ABCDEF"]), "global trades"),
    (dict(entries_today=[{"symbol": "SPY", "max_loss": 1.0}]), "global duplicate"),
])
def test_global_limits(over, needle):
    d = run_funnel(_intent(), _ctx(**over))
    assert d.failed_stage == "risk" and needle in _stage(d, "risk")["reason"]


def test_short_exposure_cap():
    run = _run(allow_short=True)
    d = run_funnel(_intent("short"), _ctx(run=run, short_exposure_usd=48_000.0))
    assert d.failed_stage == "risk" and "short_exposure" in _stage(d, "risk")["reason"]


def test_the_window_is_checked_at_entry_time():
    assert run_funnel(_intent(), _ctx()).outcome == "ready"         # decided 16:05, sent 9:45
    early = run_funnel(_intent(), _ctx(entry_at=ENTRY_AT.replace(minute=40)))
    assert early.failed_stage == "risk" and "window" in _stage(early, "risk")["reason"]


def test_early_close_moves_the_entry_cutoff():
    half_day = datetime(2026, 11, 27, 13, 0, tzinfo=NY)
    d = run_funnel(_intent(), _ctx(now_ny=half_day, entry_at=half_day))
    assert d.failed_stage == "risk" and "12:30" in _stage(d, "risk")["reason"]


def test_manual_approval_makes_a_proposal():
    cfg = pcfg.default_config()
    cfg["approval_mode"] = "manual"
    d = run_funnel(_intent(), _ctx(cfg=cfg))
    assert d.outcome == "proposed" and d.failed_stage is None
    assert _stage(d, "approval")["status"] == "warn"
    assert d.qty == 50


def test_exits_never_go_through_the_funnel():
    with pytest.raises(ValueError):
        run_funnel(replace(_intent(), kind="exit"), _ctx())


def test_journal_fields():
    d = run_funnel(_intent(), _ctx())
    row = d.journal_fields(ts="2026-10-29T16:05:00-04:00", config_hash="abc", outcome="entered")
    assert row["outcome"] == "entered" and row["config_hash"] == "abc"
    assert row["qty"] == 50 and row["max_loss"] == 200.0 and row["plan"]["stop"] == 96.0
    assert row["stages"] == d.stages and row["llm_would_block"] is None
    assert d.summary().startswith("ready: long 50 SPY")
    blocked = run_funnel(_intent(symbol="QQQ"), _ctx())
    assert blocked.summary().startswith("blocked at universe:")
