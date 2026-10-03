"""Bybit routing for -USD tickers. No live HTTP and no real secrets."""
import hashlib
import hmac

import pandas as pd
import pytest

import bybit_client
from bybit_client import BybitError, fetch_klines
from market_symbols import is_bybit_crypto, is_valid_symbol, normalize_symbol, to_bybit_spot_symbol

_DAY = 86_400_000
_T0 = 1_577_836_800_000  # 2020-01-01 00:00 UTC


def _bar(ts: int, close: float) -> list[str]:
    return [str(ts), "1", "2", "0.5", str(close), "10", "20"]


class _Resp:
    def __init__(self, rows: list):
        self._rows = rows

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"retCode": 0, "retMsg": "OK", "result": {"list": self._rows}}


def _keys(monkeypatch) -> None:
    monkeypatch.setenv("BYBIT_API_KEY", "test-key")
    monkeypatch.setenv("BYBIT_SECRET_KEY", "test-secret")


def test_symbol_routing():
    assert is_bybit_crypto("SPY") is False
    assert is_bybit_crypto("BTCUSD") is False
    assert is_bybit_crypto("btc") is True
    assert normalize_symbol("btc") == "BTC-USD"
    assert to_bybit_spot_symbol("btc") == "BTCUSDT"
    assert to_bybit_spot_symbol("ETH-USD") == "ETHUSDT"
    assert is_valid_symbol("BTC-USD") is True
    with pytest.raises(ValueError):
        to_bybit_spot_symbol("SPY")


def test_klines_drop_forming_bar_and_sort(monkeypatch):
    _keys(monkeypatch)
    t1 = _T0
    t2 = _T0 + _DAY
    monkeypatch.setattr(bybit_client.time, "time", lambda: (t2 + 60_000) / 1000)

    def fake_get(url, headers, timeout):
        assert headers["X-BAPI-API-KEY"] == "test-key"
        assert "test-secret" not in url
        query = url.split("?", 1)[1]
        payload = f"{headers['X-BAPI-TIMESTAMP']}test-key5000{query}"
        expected = hmac.new(b"test-secret", payload.encode(), hashlib.sha256).hexdigest()
        assert headers["X-BAPI-SIGN"] == expected
        assert query.index("category=") < query.index("symbol=")
        assert "symbol=BTCUSDT" in query
        return _Resp([_bar(t2, 30), _bar(t1, 10)])

    monkeypatch.setattr(bybit_client.requests, "get", fake_get)
    df = fetch_klines("BTC-USD", "1d", start="2020-01-01")
    assert list(df["Close"]) == [10.0]
    assert df.index[0] == pd.Timestamp(t1, unit="ms", tz="UTC")
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_klines_paginate(monkeypatch):
    _keys(monkeypatch)
    monkeypatch.setattr(bybit_client, "_KLINE_LIMIT", 2)
    t0, t1, t2 = _T0, _T0 + _DAY, _T0 + 2 * _DAY
    pages = [[_bar(t2, 3), _bar(t1, 2)], [_bar(t1, 2), _bar(t0, 1)]]
    calls = {"n": 0}

    urls: list[str] = []

    def fake_get(url, headers, timeout):
        urls.append(url)
        rows = pages[calls["n"]]
        calls["n"] += 1
        return _Resp(rows)

    monkeypatch.setattr(bybit_client.requests, "get", fake_get)
    df = fetch_klines("ETH-USD", "1d", start="2020-01-01", end="2020-01-05")
    assert calls["n"] == 2
    assert list(df["Close"]) == [1.0, 2.0, 3.0]
    assert any("symbol=ETHUSDT" in url for url in urls)


def test_missing_bybit_keys(monkeypatch):
    monkeypatch.setattr(bybit_client, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_SECRET_KEY", raising=False)
    with pytest.raises(BybitError, match="BYBIT_API_KEY"):
        fetch_klines("BTC-USD", "1d")


def test_lab_accepts_hyphen_ticker(monkeypatch):
    import dashboard

    monkeypatch.setattr(dashboard, "fetch_history", lambda *args, **kwargs: pd.DataFrame())
    _payload, status, err = dashboard.run_backtest_from({
        "symbol": "BTC-USD",
        "strategy": "sma_crossover",
        "timeframe": "1d",
        "fast": 10,
        "slow": 50,
    })
    assert status == 404
    assert err and "Invalid symbol" not in err


def test_fetch_history_routes_crypto_not_stocks(monkeypatch):
    import dashboard

    routed = {}

    def fake_ohlcv(symbol, timeframe="1d", start=None, end=None):
        routed["symbol"] = symbol
        routed["timeframe"] = timeframe
        routed["start"] = start
        return pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1.0]})

    monkeypatch.setattr(dashboard, "fetch_ohlcv", fake_ohlcv)
    dashboard.fetch_history("eth", {"interval": "1h", "start": "2024-01-01"})
    assert routed == {"symbol": "ETH-USD", "timeframe": "1h", "start": "2024-01-01"}

    class _Ticker:
        def __init__(self, symbol):
            routed["stock"] = symbol

        def history(self, **kwargs):
            return pd.DataFrame({"Open": [1.0]})

    monkeypatch.setattr("yfinance.Ticker", _Ticker)
    dashboard.fetch_history("SPY", {"start": "2024-01-01"})
    assert routed["stock"] == "SPY"


def test_signalbot_crypto_skips_yfinance(monkeypatch):
    import signalbot

    routed = {}

    def fake_ohlcv(symbol, timeframe="1d", start=None, end=None):
        routed["symbol"] = symbol
        routed["timeframe"] = timeframe
        return pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1.0]})

    monkeypatch.setattr(signalbot, "fetch_ohlcv", fake_ohlcv)
    df = signalbot.fetch_bars("btc", "1h")
    assert routed["symbol"] == "BTC-USD"
    assert routed["timeframe"] == "1h"
    assert len(df) == 1
