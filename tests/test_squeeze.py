"""LazyBear Squeeze Momentum: colors, squeeze state, zero cross."""
import numpy as np
import pandas as pd

import charts.squeeze as sq


def _df(closes, wick=0.0):
    """High=low=close (+wick) series; true range comes from close-to-close."""
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        rows.append({
            "Open": c,
            "High": c + wick,
            "Low": c - wick,
            "Close": c,
        })
        prev = c
    return pd.DataFrame(rows, index=idx)


def test_bar_color_helper():
    assert sq.bar_color(1.0, 0.5) == sq.BRIGHT_GREEN    # positive, rising
    assert sq.bar_color(1.0, 1.5) == sq.DARK_GREEN      # positive, fading
    assert sq.bar_color(-1.0, -0.5) == sq.PINK          # negative, accelerating
    assert sq.bar_color(-0.5, -1.0) == sq.DARK_RED      # negative, easing
    assert sq.bar_color(0.0, None) == sq.PINK


def test_squeeze_state_helper():
    # BB strictly inside KC -> squeeze on
    assert sq.squeeze_state(99.0, 101.0, 98.0, 102.0) == "on"
    # BB wide of KC on both sides -> squeeze released
    assert sq.squeeze_state(98.0, 102.0, 99.0, 101.0) == "off"
    # bands straddle -> indeterminate
    assert sq.squeeze_state(99.0, 102.0, 98.0, 101.0) == "none"


def test_ramp_momentum_positive_and_constant():
    # linear ramp: close runs the top half of its 20-bar range -> val > 0
    closes = 100.0 + 0.5 * np.arange(40)
    snap = sq.squeeze_snapshot(_df(list(closes), wick=0.1))
    assert snap["ok"] is True
    assert snap["value"] > 0
    assert snap["color"] in (sq.BRIGHT_GREEN, sq.DARK_GREEN)


def test_zigzag_is_squeezed_on():
    # +/-1 alternating closes: std=1 -> BB half 2.0; TR=2 -> KC half 3.0: on
    closes = [100.0 if i % 2 == 0 else 102.0 for i in range(40)]
    snap = sq.squeeze_snapshot(_df(closes))
    assert snap["ok"] is True
    assert snap["squeeze"] == "on"


def test_wide_range_squeeze_off():
    # quadratic run: close variance dwarfs 1.5x mean TR -> BB breaks out of KC
    closes = [float(i * i) for i in range(30)]
    snap = sq.squeeze_snapshot(_df(closes, wick=0.1))
    assert snap["ok"] is True
    assert snap["squeeze"] == "off"


def test_accelerating_run_bright_green():
    # flat base then 101 -> 103 -> 106: momentum value rises into green
    closes = [100.0] * 24 + [101.0, 103.0, 106.0]
    snap = sq.squeeze_snapshot(_df(closes))
    assert snap["ok"] is True
    assert snap["value"] > snap["prev_value"]
    assert snap["color"] == sq.BRIGHT_GREEN


def test_crash_bar_crosses_zero_down():
    # steady climb then a -40 close on the final bar -> val flips negative
    closes = [100.0 + 0.5 * i for i in range(28)] + [75.5]
    snap = sq.squeeze_snapshot(_df(closes))
    assert snap["ok"] is True
    assert snap["value"] < 0
    assert snap["zero_cross"] == "down"
    assert snap["color"] in (sq.PINK, sq.DARK_RED)


def test_not_enough_bars():
    snap = sq.squeeze_snapshot(_df([100.0] * 10))
    assert snap["ok"] is False
