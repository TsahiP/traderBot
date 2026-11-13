"""Relative strength vs a benchmark and a volume-by-price profile (POC + value area).

Ideas from mcp-trader (wshobson/mcp-trader), reworked:
- relative_strength: excess return over the benchmark per lookback, plus a
  1-99 score (50 = in line) - same formula as mcp-trader.
- volume_profile: each bar's volume is spread over the price bins it covers in
  proportion to the overlap (mcp-trader adds the bar's FULL volume to every bin
  it touches, which over-counts wide bars). The value area grows from the POC
  toward the heavier neighbour until it holds `value_area` of the volume, the
  usual market-profile construction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RS_PERIODS = (21, 63, 126, 252)


def relative_strength(df: pd.DataFrame, bench: pd.DataFrame, periods=RS_PERIODS) -> dict:
    """{'RS_21d': score, 'Return_21d': %, 'Benchmark_21d': %, 'Excess_21d': %, ...}
    on aligned daily closes; periods without enough history are left out."""
    a = df["Close"].astype(float)
    b = bench["Close"].astype(float)
    a.index = pd.DatetimeIndex(a.index).tz_localize(None).normalize()
    b.index = pd.DatetimeIndex(b.index).tz_localize(None).normalize()
    joined = pd.concat([a, b], axis=1, join="inner").dropna()
    out: dict = {}
    for p in periods:
        if len(joined) <= p:
            continue
        ret = (joined.iloc[-1, 0] / joined.iloc[-1 - p, 0] - 1) * 100
        bret = (joined.iloc[-1, 1] / joined.iloc[-1 - p, 1] - 1) * 100
        excess = ret - bret
        out[f"RS_{p}d"] = round(float(min(max(50 + excess, 1), 99)), 2)
        out[f"Return_{p}d"] = round(float(ret), 2)
        out[f"Benchmark_{p}d"] = round(float(bret), 2)
        out[f"Excess_{p}d"] = round(float(excess), 2)
    if out:
        leads = sum(1 for p in periods if out.get(f"Excess_{p}d", 0) > 0)
        counted = sum(1 for p in periods if f"Excess_{p}d" in out)
        out["leading_periods"] = f"{leads}/{counted}"
    return out


def volume_profile(df: pd.DataFrame, bins: int = 24, value_area: float = 0.70) -> dict:
    """POC, value area low/high and where the last close sits against them."""
    if df is None or len(df) < 20:
        raise ValueError("need at least 20 bars for a volume profile")
    lo_all, hi_all = float(df["Low"].min()), float(df["High"].max())
    if hi_all <= lo_all:
        raise ValueError("flat price range")
    edges = np.linspace(lo_all, hi_all, bins + 1)
    vol = np.zeros(bins)
    for low, high, v in zip(df["Low"].to_numpy(float), df["High"].to_numpy(float),
                            df["Volume"].to_numpy(float)):
        if not np.isfinite(v) or v <= 0:
            continue
        if high <= low:                       # doji bar: all volume at one price
            i = min(int(np.searchsorted(edges, low, side="right")) - 1, bins - 1)
            vol[max(i, 0)] += v
            continue
        overlap = np.clip(np.minimum(edges[1:], high) - np.maximum(edges[:-1], low), 0, None)
        vol += v * overlap / (high - low)
    total = vol.sum()
    if total <= 0:
        raise ValueError("no volume")

    poc = int(vol.argmax())
    lo_i = hi_i = poc
    held = vol[poc]
    while held / total < value_area and (lo_i > 0 or hi_i < bins - 1):
        below = vol[lo_i - 1] if lo_i > 0 else -1.0
        above = vol[hi_i + 1] if hi_i < bins - 1 else -1.0
        if above >= below:
            hi_i += 1
            held += above
        else:
            lo_i -= 1
            held += below
    mid = (edges[:-1] + edges[1:]) / 2
    val, vah = float(edges[lo_i]), float(edges[hi_i + 1])
    last = float(df["Close"].iloc[-1])
    where = "above_value" if last > vah else "below_value" if last < val else "inside_value"
    return {
        "bars": len(df),
        "point_of_control": round(float(mid[poc]), 2),
        "value_area_low": round(val, 2),
        "value_area_high": round(vah, 2),
        "value_area_pct": round(float(held / total * 100), 1),
        "last_close": round(last, 2),
        "price_vs_value": where,
        "poc_distance_pct": round((last - float(mid[poc])) / last * 100, 2),
    }
