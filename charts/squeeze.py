"""LazyBear Squeeze Momentum (analyst step 4): every number computed in code.

Bollinger 20/2 inside Keltner 20/1.5 (true range) = squeeze. The momentum
value is a 20-bar linear regression of (close - avg(avg(H20, L20), SMA20)),
colored lime/green/red/maroon like the TradingView original.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from charts.confirmations import bollinger

BB_PERIOD = 20
BB_STD = 2.0
KC_PERIOD = 20
KC_MULT = 1.5
MIN_BARS = KC_PERIOD + 2

# column colors (bright = accelerating in its direction)
BRIGHT_GREEN = "bright_green"
DARK_GREEN = "dark_green"
DARK_RED = "dark_red"
PINK = "pink"  # bright red: fresh down-thrust


def bar_color(value: float, prev: float | None) -> str:
    """LazyBear bcolor: rising/falling classification of one momentum bar."""
    if prev is None:
        return BRIGHT_GREEN if value > 0 else PINK
    if value > 0:
        return BRIGHT_GREEN if value > prev else DARK_GREEN
    if value < 0:
        return PINK if value < prev else DARK_RED
    return DARK_GREEN if value >= prev else DARK_RED


def squeeze_state(lower_bb: float, upper_bb: float, lower_kc: float, upper_kc: float) -> str:
    """on = squeeze in progress (BB inside KC), off = released, none = mixed."""
    if lower_bb > lower_kc and upper_bb < upper_kc:
        return "on"
    if lower_bb < lower_kc and upper_bb > upper_kc:
        return "off"
    return "none"


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    return pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _linreg_pred(window: np.ndarray) -> float:
    """Pine `linreg(src, len, 0)`: fitted value at the last point of the window."""
    n = window.size
    xs = np.arange(n, dtype=float)
    y_mean = float(window.mean())
    dx = xs - xs.mean()
    var = float((dx * dx).sum())
    if var == 0 or not np.isfinite(var):
        return y_mean
    slope = float((dx * (window - y_mean)).sum()) / var
    return y_mean + slope * (n - 1 - xs.mean())


def momentum_series(df: pd.DataFrame) -> pd.Series:
    """The `val` histogram series (first value ready after KC_PERIOD bars)."""
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    # inner components use every available bar so the val series starts at KC_PERIOD
    hh = high.rolling(KC_PERIOD, min_periods=1).max()
    ll = low.rolling(KC_PERIOD, min_periods=1).min()
    mid_range = (hh + ll) / 2.0
    basis = close.rolling(KC_PERIOD, min_periods=1).mean()
    src = close - (mid_range + basis) / 2.0
    return src.rolling(KC_PERIOD).apply(_linreg_pred, raw=True)


def squeeze_snapshot(df: pd.DataFrame) -> dict:
    """Current Squeeze Momentum state: value, color, squeeze on/off, zero cross."""
    if df is None or len(df) < MIN_BARS or df["Close"].isna().any():
        return {"ok": False, "error": "not enough bars"}

    close = df["Close"].astype(float)
    _mid, upper_bb, lower_bb = bollinger(close, BB_PERIOD, BB_STD)
    kc_basis = close.rolling(KC_PERIOD).mean()
    kc_range = _true_range(df).rolling(KC_PERIOD).mean()
    kc_upper = kc_basis + KC_MULT * kc_range
    kc_lower = kc_basis - KC_MULT * kc_range

    val = momentum_series(df).dropna()
    if val.empty:
        return {"ok": False, "error": "not enough bars"}

    value = float(val.iloc[-1])
    prev = float(val.iloc[-2]) if len(val) >= 2 else None
    color = bar_color(value, prev)
    prev_color = bar_color(prev, float(val.iloc[-3]) if len(val) >= 3 else None)

    cross = None
    if prev is not None:
        if prev <= 0 < value:
            cross = "up"
        elif prev >= 0 > value:
            cross = "down"

    return {
        "ok": True,
        "value": round(value, 6),
        "prev_value": None if prev is None else round(prev, 6),
        "color": color,
        "fresh_bright_green": color == BRIGHT_GREEN and prev_color != BRIGHT_GREEN,
        "squeeze": squeeze_state(
            float(lower_bb.iloc[-1]), float(upper_bb.iloc[-1]),
            float(kc_lower.iloc[-1]), float(kc_upper.iloc[-1]),
        ),
        "zero_cross": cross,
    }
