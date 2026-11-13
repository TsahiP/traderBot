"""paper.exit_plan: stop and target by rule, both sides, cent rounding away from
the entry, refusals with a reason."""
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from paper.exit_plan import Plan, Refusal, plan

NY = ZoneInfo("America/New_York")


def _bars(n=30, rng=2.0, close=100.0):
    """Flat closes with a constant high-low range -> ATR == rng."""
    idx = pd.date_range("2026-09-01", periods=n, freq="B", tz=NY)
    c = np.full(n, close)
    return pd.DataFrame({"Open": c, "High": c + rng / 2, "Low": c - rng / 2, "Close": c,
                         "Volume": np.full(n, 1e6)}, index=idx)


def test_atr_long_and_short():
    long = plan("long", 100.0, "atr:2", 2.0, bars=_bars())
    assert isinstance(long, Plan)
    assert (long.stop, long.target, long.risk_per_share) == (96.0, 108.0, 4.0)
    short = plan("short", 100.0, "atr:2", 2.0, bars=_bars())
    assert (short.stop, short.target) == (104.0, 92.0)
    assert short.reward_risk == 2.0 and "ATR" in short.stop_note


def test_pct_rule():
    p = plan("long", 250.0, "pct:1", 1.5, bars=None)
    assert (p.stop, p.target) == (247.5, 253.75)


def test_or_mid_and_or_far_from_the_opening_range():
    # the trader-exit reel: TSLA entry 364.18, range 354.47-362.18
    mid = plan("long", 364.18, "or-mid", 1.0, or_high=362.18, or_low=354.47)
    assert (mid.stop, mid.target) == (358.32, 370.04)
    assert mid.risk_per_share == pytest.approx(5.86)
    far = plan("long", 364.18, "or-far", 2.0, or_high=362.18, or_low=354.47)
    assert far.stop == 354.47
    short = plan("short", 354.00, "or-far", 2.0, or_high=362.18, or_low=354.47)
    assert short.stop == 362.18 and short.target == 337.64


def test_stop_rounds_away_from_the_entry():
    # 100 - 1.5 x 2.333 = 96.5005 -> long rounds down, short (103.4995) rounds up
    bars = _bars(rng=2.333)
    assert plan("long", 100.0, "atr:1.5", 2.0, bars=bars).stop == 96.5
    assert plan("short", 100.0, "atr:1.5", 2.0, bars=bars).stop == 103.5


def test_target_rounds_half_up():
    p = plan("long", 100.0, "pct:1.0025", 1.0, bars=None)   # stop 98.9975 -> 98.99, risk 1.01
    assert p.stop == 98.99 and p.target == 101.01
    q = plan("long", 10.005, "pct:10", 1.0, bars=None)        # stop 9.0045 -> 9.00, risk 1.005
    assert q.target == 11.01                                  # 11.01 exactly, half-up


@pytest.mark.parametrize("kwargs,why", [
    (dict(side="long", entry=100.0, rule="atr:2", target_r=2.0, bars=_bars(n=5)), "ATR"),
    (dict(side="long", entry=100.0, rule="or-mid", target_r=2.0), "opening range"),
    (dict(side="long", entry=100.0, rule="structure", target_r=2.0), "structure"),
    (dict(side="long", entry=100.0, rule="pct:0.01", target_r=2.0), "too tight"),
    (dict(side="long", entry=0.0, rule="pct:1", target_r=2.0), "entry"),
    (dict(side="long", entry=100.0, rule="pct:1", target_r=0.0), "target"),
    (dict(side="short", entry=100.0, rule="pct:60", target_r=2.0), "target"),
    (dict(side="sideways", entry=100.0, rule="pct:1", target_r=2.0), "side"),
    (dict(side="long", entry=100.0, rule="moon", target_r=2.0), "stop rule"),
])
def test_refusals_say_why(kwargs, why):
    out = plan(**kwargs)
    assert isinstance(out, Refusal)
    assert why in out.reason


def test_stop_on_the_wrong_side_of_the_entry():
    # long breakout entry below the range midpoint: or-mid would sit above it
    out = plan("long", 357.0, "or-mid", 2.0, or_high=362.18, or_low=354.47)
    assert isinstance(out, Refusal) and "not below" in out.reason
    out = plan("short", 360.0, "or-mid", 2.0, or_high=362.18, or_low=354.47)
    assert isinstance(out, Refusal) and "not above" in out.reason


def test_flat_by_refuses_a_day_entry_too_late():
    late = datetime(2026, 10, 30, 15, 55, tzinfo=NY)
    out = plan("long", 100.0, "pct:1", 2.0, now_ny=late, flat_by="15:55")
    assert isinstance(out, Refusal) and "15:55" in out.reason
    ok = plan("long", 100.0, "pct:1", 2.0, now_ny=late.replace(minute=54), flat_by="15:55")
    assert isinstance(ok, Plan) and ok.flat_by == "15:55"


def test_as_dict_round_trips_the_numbers():
    d = plan("short", 100.0, "atr:2", 2.0, bars=_bars()).as_dict()
    assert d["side"] == "short" and d["stop"] == 104.0 and d["rule"] == "atr:2"
    assert set(d) >= {"entry", "target", "risk_per_share", "target_r", "reward_risk", "stop_note", "flat_by"}
