"""PNG chart rendering for signal alerts."""
import pandas as pd

from chart_image import render_candles


def _sample_df(rows: int = 12) -> pd.DataFrame:
    idx = pd.date_range("2026-01-02", periods=rows, freq="D")
    base = 100.0
    data = []
    for i in range(rows):
        o = base + i * 0.5
        c = o + (0.3 if i % 2 == 0 else -0.2)
        h = max(o, c) + 0.4
        l = min(o, c) - 0.4
        data.append({"Open": o, "High": h, "Low": l, "Close": c})
        base = c
    return pd.DataFrame(data, index=idx)


def test_render_candles_long_with_levels():
    df = _sample_df()
    entry = float(df["Close"].iloc[-1])
    levels = {"entry": entry, "stop": entry - 2.0, "target": entry + 4.0}
    png = render_candles(df, "SPY", "1d", "Bullish engulfing", "long", levels=levels)
    assert isinstance(png, bytes)
    assert len(png) > 1024
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_candles_short_with_levels():
    df = _sample_df()
    entry = float(df["Close"].iloc[-1])
    levels = {"entry": entry, "stop": entry + 2.0, "target": entry - 4.0}
    png = render_candles(df, "SPY", "1h", "Bearish engulfing", "short", levels=levels)
    assert len(png) > 1024


def test_render_candles_levels_without_target():
    df = _sample_df()
    entry = float(df["Close"].iloc[-1])
    levels = {"entry": entry, "stop": entry - 1.0, "target": None}
    png = render_candles(df, "QQQ", "1d", "Hammer", "long", levels=levels)
    assert len(png) > 1024


def test_render_candles_without_levels():
    df = _sample_df()
    png = render_candles(df, "SPY", "1d", "Hammer", "long")
    assert len(png) > 1024
