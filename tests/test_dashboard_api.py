import json
from unittest.mock import patch
import numpy as np
import pandas as pd
import pytest

from dashboard import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_strategies_endpoint(client):
    res = client.get("/api/strategies")
    assert res.status_code == 200
    data = res.get_json()
    assert isinstance(data, list)
    strategy_ids = [s["id"] for s in data]
    assert "sma_crossover" in strategy_ids
    assert "vwap_reversion" in strategy_ids
    assert "opening_range_breakout" in strategy_ids
    assert "rsi_mean_reversion" in strategy_ids


def test_api_stats_and_trades_endpoints(client):
    res_stats = client.get("/api/stats")
    assert res_stats.status_code == 200
    stats = res_stats.get_json()
    assert "realized" in stats

    res_trades = client.get("/api/trades")
    assert res_trades.status_code == 200
    trades = res_trades.get_json()
    assert "trades" in trades
    assert isinstance(trades["trades"], list)


def test_api_backtest_run_validation_errors(client):
    # Missing / bad strategy
    res = client.get("/api/backtest/run?strategy=unknown_strat")
    assert res.status_code == 400
    assert "error" in res.get_json()

    # Fast >= slow validation
    res_sma = client.get("/api/backtest/run?strategy=sma_crossover&fast=50&slow=20")
    assert res_sma.status_code == 400
    assert "SMA periods must satisfy fast < slow" in res_sma.get_json()["error"]

    # Invalid symbol
    res_sym = client.get("/api/backtest/run?symbol=INVALID$$$SYMBOL")
    assert res_sym.status_code == 400


def test_api_backtest_run_success(client):
    # Mock yfinance Ticker.history so we don't depend on internet during test
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    mock_df = pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(10)],
            "High": [101.0 + i for i in range(10)],
            "Low": [99.0 + i for i in range(10)],
            "Close": [100.5 + i for i in range(10)],
            "Volume": [1000] * 10,
        },
        index=dates,
    )

    with patch("yfinance.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = mock_df

        res = client.get("/api/backtest/run?symbol=SPY&strategy=sma_crossover&fast=2&slow=5&qty=10&cost_per_share=0.01")
        assert res.status_code == 200
        payload = res.get_json()
        assert "meta" in payload
        assert "metrics" in payload
        assert "series" in payload
        assert "trades" in payload
        assert "markers" in payload
        assert payload["meta"]["symbol"] == "SPY"


def test_api_backtest_run_with_nan_bars_returns_valid_json(client):
    """Verify that even when yfinance returns rows with NaNs (e.g. current live bar), the response is valid RFC 8259 JSON."""
    dates = pd.date_range("2026-01-01", periods=6, freq="D")
    mock_df = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0, np.nan, 104.0, np.nan],
            "High": [101.0, 102.0, 103.0, np.nan, 105.0, np.nan],
            "Low": [99.0, 100.0, 101.0, np.nan, 103.0, np.nan],
            "Close": [100.0, 101.0, 102.0, np.nan, 104.0, np.nan],
            "Volume": [1000, 1000, 1000, 0, 1000, 0],
        },
        index=dates,
    )

    with patch("yfinance.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = mock_df

        res = client.get("/api/backtest/run?symbol=SPY&strategy=sma_crossover&fast=2&slow=3")
        assert res.status_code == 200
        raw_text = res.get_data(as_text=True)
        # Strict JSON parse check: must not contain NaN or Infinity literals
        assert ":NaN" not in raw_text
        assert ": NaN" not in raw_text
        assert ":Infinity" not in raw_text
        assert ":-Infinity" not in raw_text

        # Verify it parses cleanly with standard Python json
        parsed = json.loads(raw_text)
        assert isinstance(parsed["metrics"]["buy_hold"], (int, float))
        assert isinstance(parsed["metrics"]["cagr"], (int, float))
        assert not np.isnan(parsed["metrics"]["buy_hold"])
        assert not np.isnan(parsed["metrics"]["cagr"])


def test_api_watchlist_endpoint(client):
    res = client.get("/api/watchlist")
    assert res.status_code == 200
    data = res.get_json()
    assert "watchlist" in data
    assert isinstance(data["watchlist"], list)
    assert len(data["watchlist"]) > 0


def test_api_stock_analysis_endpoint(client):
    mock_result = {
        "symbol": "AAPL",
        "company_name": "Apple Inc.",
        "current_price": 230.5,
        "overall_score": 78.4,
        "recommendation": "BUY",
        "confidence_pct": 85.0,
        "risk_flags": [],
        "dimensions": [
            {
                "id": "momentum",
                "label": "Momentum & Trend",
                "score": 80.0,
                "summary": "Bullish",
                "metrics": {},
            }
        ],
    }
    with patch("stock_analysis.analyze_stock", return_value=mock_result):
        res = client.get("/api/stock-analysis?symbol=AAPL")
        assert res.status_code == 200
        data = res.get_json()
        assert data["symbol"] == "AAPL"
        assert data["overall_score"] == 78.4
        assert data["recommendation"] == "BUY"

    # Test invalid symbol
    res_bad = client.get("/api/stock-analysis?symbol=$$$BAD$$$")
    assert res_bad.status_code == 400


def test_api_crypto_analysis_endpoint(client):
    mock_overview = {
        "assets": [
            {
                "symbol": "BTC-USD",
                "name": "Bitcoin",
                "category": "Store of Value",
                "current_price": 65000.0,
                "composite_score": 82.0,
                "signal": "STRONG BUY",
                "btc_correlation_30d": 1.0,
                "volatility_regime": "Moderate Volatility",
            }
        ],
        "count": 1,
    }
    with patch("crypto_analysis.get_crypto_top20_overview", return_value=mock_overview):
        res = client.get("/api/crypto-analysis")
        assert res.status_code == 200
        data = res.get_json()
        assert "assets" in data
        assert data["count"] == 1
        assert data["assets"][0]["symbol"] == "BTC-USD"
