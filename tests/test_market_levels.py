"""Relative strength vs a benchmark and the proportional volume profile."""
import numpy as np
import pandas as pd
import pytest

import market_levels as ml


def _daily(closes, start="2025-01-01", tz=None):
    idx = pd.date_range(start, periods=len(closes), freq="B", tz=tz)
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1e6}, index=idx)


def test_relative_strength_excess_and_score():
    stock = _daily(np.linspace(100, 130, 300))          # +30% over the year
    bench = _daily(np.linspace(100, 110, 300), tz="America/New_York")  # tz mix is aligned
    rs = ml.relative_strength(stock, bench)
    assert set(rs) >= {"RS_21d", "Excess_252d", "leading_periods"}
    assert rs["Excess_252d"] > 0 and rs["RS_252d"] > 50
    assert rs["leading_periods"] == "4/4"


def test_relative_strength_skips_short_history():
    rs = ml.relative_strength(_daily(np.linspace(100, 90, 50)), _daily(np.linspace(100, 100, 50)))
    assert "RS_21d" in rs and "RS_63d" not in rs
    assert rs["Excess_21d"] < 0 and rs["leading_periods"] == "0/1"


def test_volume_profile_finds_the_heavy_shelf():
    closes = [100.0] * 15 + [110.0] * 10 + [120.0] * 5
    df = _daily(closes)
    df.loc[df["Close"] == 110.0, "Volume"] = 5e6          # the heavy shelf
    vp = ml.volume_profile(df, bins=20)
    assert vp["point_of_control"] == pytest.approx(110.0, abs=1.5)
    assert vp["value_area_low"] <= vp["point_of_control"] <= vp["value_area_high"]
    assert vp["value_area_pct"] >= 70.0
    assert vp["price_vs_value"] == "above_value"          # last close 120 sits above value


def test_volume_profile_spreads_volume_by_overlap():
    # one wide bar across the whole range + many narrow bars at the top:
    # the POC must stay at the top, not be dragged by the wide bar's full volume
    df = _daily([100.0] * 20)
    df["High"], df["Low"] = 101.0, 100.6
    df.iloc[0, df.columns.get_loc("Low")] = 90.0
    df.iloc[0, df.columns.get_loc("Volume")] = 3e6
    vp = ml.volume_profile(df, bins=11)
    assert vp["point_of_control"] > 99.0


def test_volume_profile_needs_bars():
    with pytest.raises(ValueError):
        ml.volume_profile(_daily([100.0] * 5))
