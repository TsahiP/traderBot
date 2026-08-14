"""Strategy registry: every backtestable logic lives here.

Each strategy exposes:
  - a compute_signals(df, params) function returning df with a "signal"
    column (+1 entry long / -1 entry short / 0 hold)
  - meta in STRATEGIES: param specs (rendered as form fields by the UI),
    supported timeframes, day-trading flags

Signals are event-based (one bar), the engine gates positions, executes
at the next bar open, and flattens at session end when flat_eod is set.
"""
import numpy as np
import pandas as pd

from strategy import compute_signals as _sma_signals


def _factory(fn):
    """Wrap a (df, params) signal function as a params->fn closure for the registry."""
    def build(params: dict):
        return lambda df: fn(df, params)

    return build


def vwap_reversion_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Mean reversion to the session VWAP.

    Long when close is >= deviation_pct below the VWAP, exit when it
    recovers to within exit_pct. Short the mirror image. VWAP resets
    every session; nothing trades overnight (engine flattens).
    """
    deviation = float(params["deviation_pct"]) / 100
    exit_pct = float(params["exit_pct"]) / 100
    df = df.copy()
    df["signal"] = 0

    for _, g in df.groupby(df.index.date):
        tp = (g["High"] + g["Low"] + g["Close"]) / 3 * g["Volume"]
        tp = tp.replace(0, np.nan)
        vwap = (tp.cumsum() / g["Volume"].cumsum())
        dev = (g["Close"] - vwap) / vwap

        state = 0  # 0 flat, 1 long, -1 short
        for i in g.index:
            if state == 0:
                if dev.at[i] <= -deviation:
                    df.at[i, "signal"] = 1
                    state = 1
                elif dev.at[i] >= deviation:
                    df.at[i, "signal"] = -1
                    state = -1
            elif state == 1:
                if dev.at[i] >= -exit_pct:
                    df.at[i, "signal"] = -1
                    state = 0
            else:
                if dev.at[i] <= exit_pct:
                    df.at[i, "signal"] = 1
                    state = 0
    return df


def opening_range_breakout_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Trade the first range_minutes of each session.

    Buy when close breaks above the opening range high, sell when it
    breaks below the low (short needs allow_short). Target is tp_mult x
    the range, stop is sl_mult x the range. Sessions whose range exceeds
    max_range_pct of the open are skipped (choppy days). One trade per
    session max; engine flattens the rest at session end.
    """
    range_minutes = int(params["range_minutes"])
    tp_mult = float(params["tp_mult"])
    sl_mult = float(params["sl_mult"])
    max_range_pct = float(params["max_range_pct"]) / 100
    df = df.copy()
    df["signal"] = 0

    for _, g in df.groupby(df.index.date):
        t0 = g.index[0]
        range_end = t0 + pd.Timedelta(minutes=range_minutes)
        in_range = g.index < range_end
        if not in_range.any() or in_range.sum() < 2:
            continue
        range_high = float(g.loc[in_range, "High"].max())
        range_low = float(g.loc[in_range, "Low"].min())
        width = range_high - range_low
        if width <= 0:
            continue
        if width / float(g["Open"].iloc[0]) > max_range_pct:
            continue

        state = 0  # 0 flat, 1 long, -1 short
        entry = None
        for i in g.loc[~in_range].index:
            close = float(df.at[i, "Close"])
            if state == 0:
                if close > range_high:
                    df.at[i, "signal"] = 1
                    state, entry = 1, close
                elif close < range_low:
                    df.at[i, "signal"] = -1
                    state, entry = -1, close
            elif state == 1:
                if close >= entry + width * tp_mult or close <= entry - width * sl_mult:
                    df.at[i, "signal"] = -1
                    state, entry = 0, None
            else:
                if close <= entry - width * tp_mult or close >= entry + width * sl_mult:
                    df.at[i, "signal"] = 1
                    state, entry = 0, None
    return df


def rsi_mean_reversion_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """RSI mean reversion.

    Long when RSI drops below oversold, exit when it crosses exit_level.
    Short when RSI tops overbought, cover when it falls to exit_level.
    """
    period = int(params["rsi_period"])
    oversold = float(params["oversold"])
    overbought = float(params["overbought"])
    exit_level = float(params["exit_level"])

    delta = df["Close"].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    rs = up.ewm(alpha=1 / period, adjust=False).mean() / down.ewm(
        alpha=1 / period, adjust=False
    ).mean()
    rsi = 100 - 100 / (1 + rs)

    df = df.copy()
    df["signal"] = 0
    state = 0
    for i in df.index:
        r = float(rsi.at[i])
        if np.isnan(r):
            continue
        if state == 0:
            if r < oversold:
                df.at[i, "signal"] = 1
                state = 1
            elif r > overbought:
                df.at[i, "signal"] = -1
                state = -1
        elif state == 1 and r > exit_level:
            df.at[i, "signal"] = -1
            state = 0
        elif state == -1 and r < exit_level:
            df.at[i, "signal"] = 1
            state = 0
    return df


def _make_sma(params: dict):
    def fn(df: pd.DataFrame) -> pd.DataFrame:
        return _sma_signals(df, int(params["fast"]), int(params["slow"]))

    return fn


STRATEGIES: dict[str, dict] = {
    "sma_crossover": {
        "label": "SMA crossover",
        "description": "Buy when the fast SMA crosses above the slow SMA, sell on the cross back. Works on any timeframe.",
        "timeframes": ["1m", "5m", "15m", "30m", "1h", "1d"],
        "flat_eod": False,
        "default_allow_short": False,
        "default_timeframe": "1d",
        "params": [
            {"key": "fast", "label": "Fast SMA", "min": 1, "max": 500, "step": 1, "default": 10, "int": True},
            {"key": "slow", "label": "Slow SMA", "min": 2, "max": 500, "step": 1, "default": 50, "int": True},
        ],
        "run": _make_sma,
    },
    "vwap_reversion": {
        "label": "VWAP reversion",
        "description": "Mean reversion to the session VWAP: fade big deviations, exit as price returns. Best in range-bound markets.",
        "timeframes": ["1m", "5m", "15m", "30m", "1h"],
        "flat_eod": True,
        "default_allow_short": True,
        "default_timeframe": "5m",
        "params": [
            {"key": "deviation_pct", "label": "Entry deviation", "min": 0.1, "max": 10, "step": 0.1, "default": 0.8, "int": False, "unit": "%"},
            {"key": "exit_pct", "label": "Exit near VWAP", "min": 0.05, "max": 5, "step": 0.05, "default": 0.2, "int": False, "unit": "%"},
        ],
        "run": _factory(vwap_reversion_signals),
    },
    "opening_range_breakout": {
        "label": "Opening range breakout",
        "description": "Trade breakouts of the first N minutes' range; target a slice of the range, stop at the far edge. Skips oversized ranges.",
        "timeframes": ["1m", "5m", "15m", "30m"],
        "flat_eod": True,
        "default_allow_short": True,
        "default_timeframe": "5m",
        "params": [
            {"key": "range_minutes", "label": "Opening range", "min": 5, "max": 120, "step": 5, "default": 15, "int": True, "unit": "min"},
            {"key": "tp_mult", "label": "Target (x range)", "min": 0.1, "max": 5, "step": 0.1, "default": 0.5, "int": False},
            {"key": "sl_mult", "label": "Stop (x range)", "min": 0.1, "max": 5, "step": 0.1, "default": 1.0, "int": False},
            {"key": "max_range_pct", "label": "Max range", "min": 0.1, "max": 3, "step": 0.05, "default": 0.55, "int": False, "unit": "%"},
        ],
        "run": _factory(opening_range_breakout_signals),
    },
    "rsi_mean_reversion": {
        "label": "RSI mean reversion",
        "description": "Fade RSI extremes: buy oversold, sell overbought, exit at the midline. Any timeframe.",
        "timeframes": ["1m", "5m", "15m", "30m", "1h", "1d"],
        "flat_eod": True,
        "default_allow_short": True,
        "default_timeframe": "15m",
        "params": [
            {"key": "rsi_period", "label": "RSI period", "min": 2, "max": 100, "step": 1, "default": 14, "int": True},
            {"key": "oversold", "label": "Oversold", "min": 1, "max": 99, "step": 1, "default": 30, "int": True},
            {"key": "overbought", "label": "Overbought", "min": 1, "max": 99, "step": 1, "default": 70, "int": True},
            {"key": "exit_level", "label": "Exit at", "min": 1, "max": 99, "step": 1, "default": 50, "int": True},
        ],
        "run": _factory(rsi_mean_reversion_signals),
    },
}


def run_strategy(strategy_id: str, df: pd.DataFrame, params: dict) -> pd.DataFrame:
    spec = STRATEGIES[strategy_id]
    return spec["run"](params)(df)


def parse_params(spec: dict, raw) -> dict:
    """Validate/coerce raw query params against a strategy's spec.

    Raises ValueError with a user-facing message on bad input.
    """
    out = {}
    for p in spec["params"]:
        key = p["key"]
        val = raw.get(key)
        if val is None:
            out[key] = p["default"]
            continue
        try:
            v = int(val) if p.get("int") else float(val)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid '{key}' - must be a number")
        if v < p["min"] or v > p["max"]:
            raise ValueError(
                f"'{key}' must be {p['min']}-{p['max']}"
                + (f" {p['unit']}" if p.get("unit") else "")
            )
        out[key] = v
    return out