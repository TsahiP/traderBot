from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
import pytest
from stock_analysis import (
    analyze_stock,
    compute_rsi,
    analyze_earnings_surprise,
    analyze_fundamentals,
    analyze_analyst_sentiment,
    analyze_momentum,
    analyze_historical_patterns,
    analyze_sentiment_and_risk,
    BASE_WEIGHTS,
)


def test_compute_rsi():
    # Constant series -> 50.0
    series_flat = pd.Series([100.0] * 30)
    assert compute_rsi(series_flat) == 50.0

    # Rising series -> high RSI
    series_up = pd.Series([100.0 + i * 2 for i in range(30)])
    assert compute_rsi(series_up) > 90.0

    # Falling series -> low RSI
    series_down = pd.Series([200.0 - i * 2 for i in range(30)])
    assert compute_rsi(series_down) < 15.0


def test_analyze_fundamentals():
    mock_info = {
        "trailingPE": 15.5,
        "forwardPE": 14.0,
        "pegRatio": 1.2,
        "profitMargins": 0.20,
        "operatingMargins": 0.25,
        "returnOnEquity": 0.22,
        "revenueGrowth": 0.12,
    }
    score, summary, metrics = analyze_fundamentals(mock_info)
    assert score is not None
    assert score > 60.0
    assert metrics["pe"] == 15.5
    assert metrics["profit_margin_pct"] == 20.0


def test_analyze_stock_mock():
    dates = pd.date_range("2025-01-01", periods=100, freq="D")
    mock_hist = pd.DataFrame(
        {
            "Open": [150.0 + i * 0.5 for i in range(100)],
            "High": [152.0 + i * 0.5 for i in range(100)],
            "Low": [149.0 + i * 0.5 for i in range(100)],
            "Close": [151.0 + i * 0.5 for i in range(100)],
            "Volume": [1_000_000] * 100,
        },
        index=dates,
    )

    mock_info = {
        "shortName": "Apple Inc.",
        "trailingPE": 22.0,
        "forwardPE": 20.0,
        "targetMeanPrice": 220.0,
        "recommendationKey": "buy",
        "numberOfAnalystOpinions": 35,
        "sector": "Technology",
    }

    with patch("yfinance.Ticker") as mock_ticker:
        instance = MagicMock()
        instance.history.return_value = mock_hist
        instance.info = mock_info
        instance.get_earnings_dates.return_value = pd.DataFrame(
            {
                "Reported EPS": [1.5, 1.4, 1.3, 1.2],
                "EPS Estimate": [1.4, 1.3, 1.2, 1.1],
                "Surprise(%)": [7.1, 7.6, 8.3, 9.0],
            }
        )
        mock_ticker.return_value = instance

        result = analyze_stock("AAPL")
        assert result["symbol"] == "AAPL"
        assert result["overall_score"] > 50.0
        assert len(result["dimensions"]) == 8
        assert result["recommendation"] in ["BUY", "STRONG BUY", "HOLD", "SELL", "STRONG SELL"]
