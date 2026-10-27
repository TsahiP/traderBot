"""Analysis chart PNGs: candles + SMC overlay + P/E/D lines + squeeze panel."""
import math

import pandas as pd
import pytest

from analysis_charts import render_analysis_chart


def _sample_df(rows: int = 140) -> pd.DataFrame:
    """Trend + sine wave so swings, squeezes and both momentum signs appear."""
    idx = pd.date_range("2026-05-04", periods=rows, freq="D")
    rows_out = []
    for i in range(rows):
        c = 100.0 + 0.05 * i + 6.0 * math.sin(2 * math.pi * i / 25)
        o = c - 0.3 * math.sin(2 * math.pi * i / 13 + 1.0)
        h = max(o, c) + abs(0.4 * math.cos(2 * math.pi * i / 17)) + 0.2
        l = min(o, c) - abs(0.4 * math.sin(2 * math.pi * i / 19)) - 0.2
        rows_out.append({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 10_000})
    return pd.DataFrame(rows_out, index=idx)


def test_render_analysis_chart_daily():
    png = render_analysis_chart(_sample_df(), "NVDA", "1d")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 5000


def test_render_analysis_chart_intraday_short_history():
    png = render_analysis_chart(_sample_df(64), "NVDA", "15m")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 5000


def test_render_analysis_chart_not_enough_bars():
    with pytest.raises(ValueError):
        render_analysis_chart(_sample_df(12), "NVDA", "15m")
