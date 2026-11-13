"""Backtest engine: bracket stop/target exits and trade-quality metrics."""
import math

import numpy as np
import pandas as pd
import pytest

import core.engine as engine


def _bars(closes, highs=None, lows=None, opens=None):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "Open": np.asarray(opens if opens is not None else c, dtype=float),
        "High": np.asarray(highs if highs is not None else c + 1, dtype=float),
        "Low": np.asarray(lows if lows is not None else c - 1, dtype=float),
        "Close": c, "Volume": 1000.0,
    }, index=idx)


def test_bracket_hit_rules():
    bar = {"Open": 100.0, "High": 106.0, "Low": 94.0}
    assert engine.bracket_hit("long", bar, 95.0, 105.0) == (95.0, "stop")      # both touched -> stop
    assert engine.bracket_hit("long", {"Open": 93.0, "High": 94.0, "Low": 92.0}, 95.0, 105.0) == (93.0, "stop")  # gap
    assert engine.bracket_hit("long", {"Open": 100.0, "High": 106.0, "Low": 99.0}, 95.0, 105.0) == (105.0, "target")
    assert engine.bracket_hit("short", {"Open": 100.0, "High": 101.0, "Low": 94.0}, 105.0, 95.0) == (95.0, "target")
    assert engine.bracket_hit("long", {"Open": 100.0, "High": 101.0, "Low": 99.0}, 95.0, 105.0) is None


def _sma_cross_up_then_drop(drop_to: float) -> pd.DataFrame:
    """Down 60 bars, up 15 (fast crosses above slow), then a sharp drop."""
    closes = list(np.linspace(120, 100, 60)) + list(np.linspace(101, 125, 15)) + [drop_to] * 5
    return _bars(closes)


def test_stop_exit_is_recorded_with_r_multiple():
    df = _sma_cross_up_then_drop(80.0)
    _, trades = engine.run_backtest(df, params={"fast": 5, "slow": 20}, qty=10,
                                    stop_atr_mult=2.0, target_r=10.0)
    stops = trades[trades["exit_type"] == "stop"]
    assert len(stops) >= 1
    t = stops.iloc[0]
    assert t["exit_price"] <= t["stop"] + 1e-9           # filled at the stop or a gap below it
    assert t["r_multiple"] <= -1.0 + 1e-9


def test_target_exit_hits_before_signal_exit():
    closes = list(np.linspace(120, 100, 60)) + list(np.linspace(101, 160, 30))
    df = _bars(closes)
    _, trades = engine.run_backtest(df, params={"fast": 5, "slow": 20}, qty=10,
                                    stop_atr_mult=2.0, target_r=1.0)
    targets = trades[trades["exit_type"] == "target"]
    assert len(targets) >= 1
    assert targets.iloc[0]["r_multiple"] == pytest.approx(1.0)


def test_brackets_off_keeps_old_behaviour():
    df = _sma_cross_up_then_drop(80.0)
    _, with_off = engine.run_backtest(df, params={"fast": 5, "slow": 20}, qty=10)
    assert set(with_off["exit_type"]) <= {"signal", "open"}
    assert with_off["r_multiple"].isna().all()


def test_trade_quality_and_sharpe():
    trades = pd.DataFrame({
        "pnl": [100.0, -50.0, 30.0, -20.0, 999.0],
        "exit_type": ["target", "stop", "signal", "stop", "open"],
        "r_multiple": [2.0, -1.0, 0.6, -1.0, None],
    })
    q = engine.trade_quality(trades)
    assert q["profit_factor"] == pytest.approx(130 / 70, abs=1e-4)
    assert q["expectancy"] == pytest.approx(15.0)
    assert q["avg_win"] == pytest.approx(65.0) and q["avg_loss"] == pytest.approx(-35.0)
    assert q["avg_r"] == pytest.approx(0.15)

    flat = pd.Series([100.0] * 10, index=pd.date_range("2026-01-01", periods=10))
    assert engine.sharpe_ratio(flat) is None
    rising = pd.Series(100 * np.cumprod(1 + np.r_[0.01, 0.02, 0.0, 0.01, 0.015]),
                       index=pd.date_range("2026-01-01", periods=5))
    s = engine.sharpe_ratio(rising)
    assert s is not None and s > 0 and math.isfinite(s)


def test_compute_metrics_includes_new_fields():
    df = _sma_cross_up_then_drop(80.0)
    curve, trades = engine.run_backtest(df, params={"fast": 5, "slow": 20}, qty=10,
                                        stop_atr_mult=2.0, target_r=2.0)
    m = engine.compute_metrics(curve, trades, 100_000.0)
    for key in ("profit_factor", "expectancy", "avg_win", "avg_loss", "avg_r", "sharpe"):
        assert key in m
