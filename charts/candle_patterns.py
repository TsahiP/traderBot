"""Japanese candlestick patterns - the strong reversal shapes only.

Each detector takes an OHLCV DataFrame (columns Open/High/Low/Close,
datetime index) and returns True ONLY when the pattern completes exactly on
the last bar. That keeps live alerts event-based: a shape that finished two
bars ago is not re-signaled.
"""
import pandas as pd


def _last(df: pd.DataFrame, n: int) -> pd.DataFrame | None:
    """Return the last n bars, or None when there are too few / NaNs."""
    if len(df) < n:
        return None
    tail = df.iloc[-n:]
    if tail[["Open", "High", "Low", "Close"]].isna().any().any():
        return None
    return tail


def bullish_engulfing(df: pd.DataFrame) -> bool:
    """Bearish bar followed by a bullish bar whose body engulfs it."""
    b = _last(df, 2)
    if b is None:
        return False
    o1, c1 = float(b["Open"].iloc[0]), float(b["Close"].iloc[0])
    o2, c2 = float(b["Open"].iloc[1]), float(b["Close"].iloc[1])
    return bool(c1 < o1 and c2 > o2 and o2 <= c1 and c2 >= o1)


def bearish_engulfing(df: pd.DataFrame) -> bool:
    """Bullish bar followed by a bearish bar whose body engulfs it."""
    b = _last(df, 2)
    if b is None:
        return False
    o1, c1 = float(b["Open"].iloc[0]), float(b["Close"].iloc[0])
    o2, c2 = float(b["Open"].iloc[1]), float(b["Close"].iloc[1])
    return bool(c1 > o1 and c2 < o2 and o2 >= c1 and c2 <= o1)


def morning_star(df: pd.DataFrame) -> bool:
    """Long bearish bar, small star in its lower half, bullish close above the first body's midpoint."""
    b = _last(df, 3)
    if b is None:
        return False
    o1, c1, h1, l1 = (float(b["Open"].iloc[0]), float(b["Close"].iloc[0]),
                      float(b["High"].iloc[0]), float(b["Low"].iloc[0]))
    o2, c2 = float(b["Open"].iloc[1]), float(b["Close"].iloc[1])
    o3, c3 = float(b["Open"].iloc[2]), float(b["Close"].iloc[2])

    body1 = abs(c1 - o1)
    if not (c1 < o1 and body1 >= 0.5 * (h1 - l1)):
        return False
    mid1 = (o1 + c1) / 2
    star_body = abs(c2 - o2)
    if not (star_body <= 0.5 * body1 and (o2 + c2) / 2 < mid1):
        return False
    return bool(c3 > o3 and c3 > mid1)


def evening_star(df: pd.DataFrame) -> bool:
    """Long bullish bar, small star in its upper half, bearish close below the first body's midpoint."""
    b = _last(df, 3)
    if b is None:
        return False
    o1, c1, h1, l1 = (float(b["Open"].iloc[0]), float(b["Close"].iloc[0]),
                      float(b["High"].iloc[0]), float(b["Low"].iloc[0]))
    o2, c2 = float(b["Open"].iloc[1]), float(b["Close"].iloc[1])
    o3, c3 = float(b["Open"].iloc[2]), float(b["Close"].iloc[2])

    body1 = abs(c1 - o1)
    if not (c1 > o1 and body1 >= 0.5 * (h1 - l1)):
        return False
    mid1 = (o1 + c1) / 2
    star_body = abs(c2 - o2)
    if not (star_body <= 0.5 * body1 and (o2 + c2) / 2 > mid1):
        return False
    return bool(c3 < o3 and c3 < mid1)


def three_white_soldiers(df: pd.DataFrame) -> bool:
    """Three solid bullish bars with strictly rising closes; each open stays inside the prior bar's range."""
    b = _last(df, 3)
    if b is None:
        return False
    prev_close, prev_high = None, None
    for i in range(3):
        o, h, l, c = (float(b["Open"].iloc[i]), float(b["High"].iloc[i]),
                      float(b["Low"].iloc[i]), float(b["Close"].iloc[i]))
        body = c - o
        if not (c > o and body >= 0.5 * (h - l)):
            return False
        if prev_close is not None and not (c > prev_close and o < prev_high):
            return False
        prev_close, prev_high = c, h
    return True


def three_black_crows(df: pd.DataFrame) -> bool:
    """Three solid bearish bars with strictly falling closes; each open stays inside the prior bar's range."""
    b = _last(df, 3)
    if b is None:
        return False
    prev_close, prev_low = None, None
    for i in range(3):
        o, h, l, c = (float(b["Open"].iloc[i]), float(b["High"].iloc[i]),
                      float(b["Low"].iloc[i]), float(b["Close"].iloc[i]))
        body = o - c
        if not (c < o and body >= 0.5 * (h - l)):
            return False
        if prev_close is not None and not (c < prev_close and o > prev_low):
            return False
        prev_close, prev_low = c, l
    return True


PATTERNS: dict[str, dict] = {
    "bullish_engulfing": {"label": "Bullish engulfing", "direction": "long", "bars": 2},
    "bearish_engulfing": {"label": "Bearish engulfing", "direction": "short", "bars": 2},
    "morning_star": {"label": "Morning star", "direction": "long", "bars": 3},
    "evening_star": {"label": "Evening star", "direction": "short", "bars": 3},
    "three_white_soldiers": {"label": "Three white soldiers", "direction": "long", "bars": 3},
    "three_black_crows": {"label": "Three black crows", "direction": "short", "bars": 3},
}

DETECTORS: dict[str, callable] = {
    "bullish_engulfing": bullish_engulfing,
    "bearish_engulfing": bearish_engulfing,
    "morning_star": morning_star,
    "evening_star": evening_star,
    "three_white_soldiers": three_white_soldiers,
    "three_black_crows": three_black_crows,
}


def detect_all(df: pd.DataFrame, pattern_ids: list[str]) -> list[str]:
    """Ids of the enabled patterns that complete on df's last bar."""
    return [pid for pid in pattern_ids if DETECTORS[pid](df)]
