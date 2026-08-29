from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
import pytest
from crypto_analysis import (
    analyze_single_crypto,
    classify_volatility_regime,
    compute_btc_correlation,
    compute_rsi,
    get_crypto_top20_overview,
    TOP_20_CRYPTOS,
)


def test_classify_volatility_regime():
    regime, desc = classify_volatility_regime(35.0)
    assert regime == "Low Volatility"

    regime, desc = classify_volatility_regime(55.0)
    assert regime == "Moderate Volatility"

    regime, desc = classify_volatility_regime(85.0)
    assert regime == "High Volatility"

    regime, desc = classify_volatility_regime(120.0)
    assert regime == "Extreme Volatility"


def test_compute_btc_correlation():
    # Perfectly correlated series
    btc_returns = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.02, -0.01, 0.02] * 4)
    asset_returns = btc_returns * 1.5
    corr = compute_btc_correlation(asset_returns, btc_returns, window=30)
    assert pytest.approx(corr, 0.01) == 1.0

    # Inversely correlated series
    asset_inv = -btc_returns
    corr_inv = compute_btc_correlation(asset_inv, btc_returns, window=30)
    assert pytest.approx(corr_inv, 0.01) == -1.0


def test_analyze_single_crypto_mock():
    dates = pd.date_range("2025-01-01", periods=100, freq="D")
    mock_hist = pd.DataFrame(
        {
            "Open": [2000.0 + i * 10 for i in range(100)],
            "High": [2050.0 + i * 10 for i in range(100)],
            "Low": [1980.0 + i * 10 for i in range(100)],
            "Close": [2020.0 + i * 10 for i in range(100)],
            "Volume": [50_000] * 100,
        },
        index=dates,
    )
    btc_returns = pd.Series([0.01] * 99, index=dates[1:])

    res = analyze_single_crypto("ETH-USD", hist=mock_hist, info={"marketCap": 300_000_000_000}, btc_returns=btc_returns)
    assert res["symbol"] == "ETH-USD"
    assert res["name"] == "Ethereum"
    assert res["category"] == "Smart Contract L1"
    assert res["market_cap_class"] == "Mega Cap"
    assert "volatility_regime" in res
    assert "composite_score" in res
    assert res["signal"] in ["BUY", "STRONG BUY", "HOLD", "SELL", "STRONG SELL"]
