import numpy as np
import pandas as pd
import pytest

import strategies
from strategy import compute_signals as sma_compute_signals, latest_signal


def test_sma_crossover_signals():
    # Construct 10 bars where price goes up and then down
    prices = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 14.0, 13.0, 12.0, 11.0]
    dates = pd.date_range("2026-01-01", periods=len(prices), freq="D")
    df = pd.DataFrame({"Close": prices, "Open": prices, "High": prices, "Low": prices, "Volume": [100] * len(prices)}, index=dates)

    res = sma_compute_signals(df, fast=2, slow=4)
    assert "signal" in res.columns
    assert "sma_fast" in res.columns
    assert "sma_slow" in res.columns

    # Verify signals are only 1, -1, 0
    assert set(res["signal"].unique()).issubset({1, -1, 0})
    # Verify latest signal returns non-zero integer or 0
    sig = latest_signal(res)
    assert isinstance(sig, int)


def test_vwap_reversion_zero_volume_resilience():
    """Verify that zero-volume bars do not cause permanent NaN VWAP propagation."""
    dates = [f"2026-01-01 {h:02d}:00" for h in range(9, 16)]
    prices = [100.0, 101.0, 95.0, 95.0, 96.0, 99.0, 100.0]
    # Bar at 11:00 has 0 volume
    volumes = [1000, 1000, 0, 500, 1000, 1000, 1000]
    df = pd.DataFrame(
        {
            "Open": prices,
            "High": prices,
            "Low": prices,
            "Close": prices,
            "Volume": volumes,
        },
        index=pd.to_datetime(dates),
    )

    params = {"deviation_pct": 2.0, "exit_pct": 0.5}
    res = strategies.vwap_reversion_signals(df, params, allow_short=True)
    assert not res["signal"].isna().any()
    # At price 95 (deviation > 2%), should emit buy signal 1
    assert 1 in res["signal"].values


def test_strategy_allow_short_flag():
    """Verify stateful strategies do not emit short entries or rogue covers when allow_short=False."""
    # RSI: prices spike upwards causing RSI > 70
    dates = pd.date_range("2026-01-01 09:30", periods=20, freq="15min")
    prices = [100 + i * 5 for i in range(20)]
    df = pd.DataFrame(
        {"Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": [1000] * 20},
        index=dates,
    )

    params = {"rsi_period": 5, "oversold": 30, "overbought": 70, "exit_level": 50}

    # With allow_short=False, no -1 short signals should be generated
    res_no_short = strategies.rsi_mean_reversion_signals(df, params, allow_short=False)
    assert (res_no_short["signal"] == -1).sum() == 0

    # With allow_short=True, overbought will trigger short entry (-1)
    res_with_short = strategies.rsi_mean_reversion_signals(df, params, allow_short=True)
    assert (res_with_short["signal"] == -1).sum() > 0


def test_opening_range_breakout_allow_short():
    """Verify ORB only shorts below range low if allow_short=True."""
    # 9:30 to 11:00 in 5min bars
    dates = pd.date_range("2026-01-01 09:30", periods=15, freq="5min")
    # Opening range (first 15 min / 3 bars) 100 to 102. Then breakdowns to 95.
    prices = [100.0, 102.0, 101.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0, 92.0, 91.0, 90.0, 89.0, 88.0, 87.0]
    df = pd.DataFrame(
        {"Open": prices, "High": [p + 0.5 for p in prices], "Low": [p - 0.5 for p in prices], "Close": prices, "Volume": [1000] * 15},
        index=dates,
    )
    params = {"range_minutes": 15, "tp_mult": 1.0, "sl_mult": 1.0, "max_range_pct": 5.0}

    res_no_short = strategies.opening_range_breakout_signals(df, params, allow_short=False)
    assert (res_no_short["signal"] == -1).sum() == 0

    res_short = strategies.opening_range_breakout_signals(df, params, allow_short=True)
    assert (res_short["signal"] == -1).sum() > 0


def test_parse_params_validation():
    spec = strategies.STRATEGIES["sma_crossover"]
    # Valid
    p = strategies.parse_params(spec, {"fast": "15", "slow": "60"})
    assert p["fast"] == 15 and p["slow"] == 60

    # Out of bounds
    with pytest.raises(ValueError):
        strategies.parse_params(spec, {"fast": "-5"})
