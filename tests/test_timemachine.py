"""Time Machine: full-session pool, seeded odds, verdict rule, refusals."""
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

import timemachine as tm
from market_calendar import NY, is_trading_day


def _sessions(n_days=30, drift=0.0, vol=0.002, seed=1, end=datetime(2026, 10, 26)):
    """n_days full NYSE sessions of synthetic 5m bars ending before `end`."""
    rng = np.random.default_rng(seed)
    days, d = [], end.date() - timedelta(days=1)
    while len(days) < n_days:
        if is_trading_day(d) and d.month != 11:   # skip early-close months for simplicity
            days.append(d)
        d -= timedelta(days=1)
    rows, idx, price = [], [], 100.0
    for day in sorted(days):
        for k in range(tm.SLOTS):
            ts = datetime.combine(day, time(9, 30), NY) + timedelta(minutes=5 * k)
            o = price
            c = o * (1 + drift + vol * rng.standard_normal())
            h, l = max(o, c) * (1 + vol / 2), min(o, c) * (1 - vol / 2)
            rows.append((o, h, l, c, 1000.0))
            idx.append(ts)
            price = c
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(idx))


AT = datetime(2026, 10, 26, 10, 0, tzinfo=NY)


def test_slot_of():
    assert tm.slot_of(time(9, 30)) == 0 and tm.slot_of(time(15, 55)) == 77


def test_pool_keeps_only_full_sessions():
    df = _sessions(25)
    partial_day = df.index[-1].date()
    df = df[~((df.index.date == partial_day) & (df.index.time > time(12, 0)))]
    pool, dates = tm.session_pool(df, before=AT)
    assert pool.shape[1:] == (78, 4)
    assert partial_day not in dates and len(dates) == 24


def test_replay_is_seeded_and_probabilities_sum_to_one():
    df = _sessions(30)
    kw = dict(entry=100.0, stop=99.0, target=102.0, at=AT, runs=2000)
    a, b = tm.replay(df, **kw), tm.replay(df, **kw)
    assert a == b
    assert a["p_target"] + a["p_stop"] + a["p_neither"] == pytest.approx(1.0, abs=1e-3)
    assert sum(a["history"].values()) == a["sessions"] == 30
    assert a["r_target"] == 2.0
    assert "Time Machine 2,000x/30d" in tm.format_line(a)


def test_drift_shows_up_in_the_odds():
    up = tm.replay(_sessions(30, drift=0.0008), entry=100.0, stop=99.0, target=101.0, at=AT, runs=3000)
    down = tm.replay(_sessions(30, drift=-0.0008), entry=100.0, stop=99.0, target=101.0, at=AT, runs=3000)
    assert up["p_target"] > up["p_stop"] and down["p_stop"] > down["p_target"]
    short = tm.replay(_sessions(30, drift=-0.0008), entry=100.0, stop=101.0, target=99.0,
                      at=AT, side="short", runs=3000)
    assert short["p_target"] > short["p_stop"]


def test_verdict_rule():
    # 2R plan: break-even needs 33% of decided replays
    assert tm.verdict(0.241, 0.505, 2.0) == "Close to break-even"
    assert tm.verdict(0.50, 0.50, 1.0) == "Close to a coin flip"
    assert tm.verdict(0.60, 0.40, 1.0) == "Leans toward the target"
    assert tm.verdict(0.20, 0.80, 1.0) == "Strongly toward the stop"


def test_refusals():
    df = _sessions(10)
    with pytest.raises(tm.TimeMachineError, match="full sessions"):
        tm.replay(df, entry=100.0, stop=99.0, target=102.0, at=AT)
    df = _sessions(25)
    with pytest.raises(tm.TimeMachineError, match="stop < entry"):
        tm.replay(df, entry=100.0, stop=101.0, target=102.0, at=AT)
    with pytest.raises(tm.TimeMachineError, match="outside the session"):
        tm.replay(df, entry=100.0, stop=99.0, target=102.0, at=AT.replace(hour=17))
