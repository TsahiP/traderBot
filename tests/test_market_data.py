"""Market symbol routing and data provider selection."""
from unittest.mock import patch

import pandas as pd
import pytest

import market_data
from market_symbols import is_crypto_symbol, to_bybit_spot_symbol


def test_is_crypto_symbol():
    assert is_crypto_symbol("BTC-USD")
    assert is_crypto_symbol("btc-usd")
    assert not is_crypto_symbol("SPY")
    assert not is_crypto_symbol("BRK.B")


def test_to_bybit_spot_symbol():
    assert to_bybit_spot_symbol("BTC-USD") == "BTCUSDT"
    assert to_bybit_spot_symbol("eth-usd") == "ETHUSDT"
    with pytest.raises(ValueError):
        to_bybit_spot_symbol("SPY")


def test_bybit_kline_parse():
    from bybit_client import _parse_kline_rows

    rows = [
        ["1700000000000", "2", "3", "1", "2.5", "100", "200"],
        ["1699990000000", "1", "2", "0.5", "1.5", "50", "75"],
    ]
    df = _parse_kline_rows(rows)
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 2
    assert df["Close"].iloc[0] == 1.5
    assert df["Close"].iloc[-1] == 2.5


@patch("market_data.fetch_klines")
def test_fetch_history_routes_crypto(mock_bybit):
    mock_bybit.return_value = pd.DataFrame(
        {"Open": [1], "High": [1], "Low": [1], "Close": [1], "Volume": [1]},
        index=pd.to_datetime(["2024-01-01"]),
    )
    df = market_data.fetch_history("BTC-USD", {"start": "2024-01-01"})
    mock_bybit.assert_called_once()
    assert not df.empty


@patch("market_data._fetch_yfinance")
def test_fetch_history_routes_equity(mock_yf):
    mock_yf.return_value = pd.DataFrame()
    market_data.fetch_history("SPY", {"start": "2024-01-01"})
    mock_yf.assert_called_once_with("SPY", {"start": "2024-01-01"})


@patch("market_data.fetch_recent_klines")
def test_fetch_recent_bars_crypto(mock_recent):
    idx = pd.to_datetime(["2024-01-01", "2024-01-02"])
    mock_recent.return_value = pd.DataFrame(
        {"Open": [1, 2], "High": [1, 2], "Low": [1, 2], "Close": [1, 2], "Volume": [1, 1]},
        index=idx,
    )
    df = market_data.fetch_recent_bars("ETH-USD", "1d")
    mock_recent.assert_called_once()
    assert len(df) >= 1
