"""paper.runs: the setup state machine and the daily strategy run (swing_sma)."""
import numpy as np
import pandas as pd
import pytest

from paper import config as pcfg
from paper.runs import (IllegalTransition, Intent, Run, Setup, SetupState as S, evaluate_daily,
                        new_setup, runs_from_config, transition)
from strategy import compute_signals

NY = "America/New_York"


def _swing_sma(**over) -> Run:
    raw = next(r for r in pcfg.default_config()["runs"] if r["id"] == "swing_sma")
    return Run.from_config({**raw, **over})


def _bars(direction: str, cross_on_last: bool = True) -> pd.DataFrame:
    """Daily bars whose SMA 10/50 crosses (up or down); cut so the cross is on
    the last bar, or followed by a few more bars."""
    if direction == "up":
        close = np.r_[np.linspace(110, 100, 70), np.linspace(101, 120, 10)]
    else:
        close = np.r_[np.linspace(100, 110, 70), np.linspace(109, 90, 10)]
    idx = pd.date_range("2026-06-01", periods=len(close), freq="B", tz=NY)
    df = pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1, "Close": close,
                       "Volume": 1e6}, index=idx)
    sig = compute_signals(df, 10, 50)["signal"]
    last = int(np.flatnonzero(sig.to_numpy() != 0)[-1])
    return df.iloc[: last + 1] if cross_on_last else df.iloc[: last + 4]


# ---------------------------------------------------------------- state machine

def test_the_happy_path_is_legal():
    s = new_setup("swing_sma", "SPY", "2026-10-30", S.IDLE)
    for state in (S.WAITING, S.TRIGGERED, S.ENTERED, S.MANAGING, S.EXITED):
        s = transition(s, state)
    assert s.state is S.EXITED


def test_manual_approval_path():
    s = transition(new_setup("day_orb", "TSLA", "2026-10-30", S.TRIGGERED), S.PROPOSED)
    assert transition(s, S.ENTERED).state is S.ENTERED
    assert transition(s, S.EXPIRED, sentence="proposal timed out").sentence == "proposal timed out"
    assert transition(s, S.BLOCKED, sentence="rejected in the UI").state is S.BLOCKED


def test_an_entry_that_never_filled_can_be_blocked():
    s = new_setup("swing_sma", "SPY", "2026-10-30", S.ENTERED)
    assert transition(s, S.BLOCKED, sentence="entry order canceled at Alpaca").state is S.BLOCKED


def test_a_state_can_update_its_own_details():
    s = new_setup("day_orb", "TSLA", "2026-10-30", S.WAITING, level=362.18, distance_pct=-0.3)
    s2 = transition(s, S.WAITING, distance_pct=-0.1, sentence="$0.20 under 362.18")
    assert s2.distance_pct == -0.1 and s2.level == 362.18 and s2.sentence == "$0.20 under 362.18"


@pytest.mark.parametrize("start,end", [
    (S.TRIGGERED, S.WAITING),      # one trigger per setup per day
    (S.ENTERED, S.WAITING),
    (S.EXITED, S.ENTERED),
    (S.EXPIRED, S.WAITING),
    (S.BLOCKED, S.TRIGGERED),
    (S.IDLE, S.ENTERED),
])
def test_illegal_moves_raise(start, end):
    with pytest.raises(IllegalTransition):
        transition(new_setup("r", "SPY", "2026-10-30", start), end)


def test_setup_round_trips_through_a_journal_row():
    s = new_setup("swing_sma", "SPY", "2026-10-30", S.WAITING, side="long", level=101.5,
                  distance_pct=-0.3, sentence="fast SMA 0.30% under slow", detail={"x": 1})
    row = s.as_row()
    assert row["state"] == "WAITING"
    assert Setup.from_row({**row, "updated_at": "t"}) == s


# ---------------------------------------------------------------- runs from config

def test_runs_from_config_keeps_enabled_runs():
    runs = runs_from_config(pcfg.default_config())
    assert [r.id for r in runs] == ["swing_sma"]
    r = runs[0]
    assert r.style == "swing" and r.symbols == ("SPY",) and r.exit["stop"] == "atr:2"
    assert len(runs_from_config(pcfg.default_config(), enabled_only=False)) == 5


# ---------------------------------------------------------------- swing_sma

def test_fresh_cross_up_is_a_long_entry():
    bars = _bars("up")
    ev = evaluate_daily(_swing_sma(), "SPY", bars)
    assert ev.state is S.TRIGGERED
    assert ev.intents == [Intent(kind="entry", run_id="swing_sma", symbol="SPY", side="long",
                                 signal_bar=str(bars.index[-1].date()),
                                 ref_price=float(bars["Close"].iloc[-1]),
                                 reason=ev.intents[0].reason)]
    assert "crossed above" in ev.intents[0].reason
    assert ev.signal_bar == str(bars.index[-1].date())


def test_a_signal_is_acted_on_once():
    bars = _bars("up")
    ev = evaluate_daily(_swing_sma(), "SPY", bars, acted_signal_bar=str(bars.index[-1].date()))
    assert ev.intents == [] and ev.state is S.WAITING


def test_an_old_cross_is_not_an_entry():
    ev = evaluate_daily(_swing_sma(), "SPY", _bars("up", cross_on_last=False))
    assert ev.intents == []
    assert ev.state is S.WAITING and "above" in ev.sentence


def test_warmup():
    ev = evaluate_daily(_swing_sma(), "SPY", _bars("up").iloc[:30])
    assert ev.intents == [] and ev.state is S.IDLE and "warming up" in ev.sentence


def test_cross_down_flat_is_short_only_when_allowed():
    bars = _bars("down")
    assert evaluate_daily(_swing_sma(), "SPY", bars).intents == []
    ev = evaluate_daily(_swing_sma(allow_short=True), "SPY", bars)
    assert [(i.kind, i.side) for i in ev.intents] == [("entry", "short")]


def test_holding_long_exits_on_the_opposite_signal_even_if_old():
    for fresh in (True, False):
        ev = evaluate_daily(_swing_sma(), "SPY", _bars("down", cross_on_last=fresh), position_side="long")
        assert [(i.kind, i.side) for i in ev.intents] == [("exit", "long")]


def test_reversal_exits_then_enters_short():
    ev = evaluate_daily(_swing_sma(allow_short=True), "SPY", _bars("down"), position_side="long")
    assert [(i.kind, i.side) for i in ev.intents] == [("exit", "long"), ("entry", "short")]


def test_holding_with_the_trend_is_managing():
    ev = evaluate_daily(_swing_sma(), "SPY", _bars("up", cross_on_last=False), position_side="long")
    assert ev.intents == [] and ev.state is S.MANAGING and "holding long" in ev.sentence


def test_no_exit_when_the_run_does_not_exit_on_signals():
    run = _swing_sma(exit={"stop": "atr:2", "target_r": 2.0, "exit_on_opposite_signal": False})
    ev = evaluate_daily(run, "SPY", _bars("down"), position_side="long")
    assert ev.intents == []


def test_detail_carries_the_numbers_for_the_ui():
    ev = evaluate_daily(_swing_sma(), "SPY", _bars("up", cross_on_last=False))
    assert set(ev.detail) >= {"close", "sma_fast", "sma_slow", "last_signal", "last_signal_bar"}
    assert ev.distance_pct == pytest.approx(
        (ev.detail["sma_fast"] - ev.detail["sma_slow"]) / ev.detail["sma_slow"] * 100, abs=1e-3)
