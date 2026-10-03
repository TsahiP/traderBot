"""OHLCV router: `-USD` tickers from Bybit spot, other tickers from yfinance."""
import time
from typing import Literal

import pandas as pd

from bybit_client import fetch_klines
from market_symbols import is_bybit_crypto, normalize_symbol

OhlcvSource = Literal["bybit", "yfinance"]

_YF_RETRY_ATTEMPTS = 3
_YF_RETRY_SLEEP_S = 0.8


def ohlcv_source(symbol: str) -> OhlcvSource:
    """Which feed supplies bars for this ticker (same rule as fetch_ohlcv)."""
    return "bybit" if is_bybit_crypto(normalize_symbol(symbol)) else "yfinance"


def fetch_yfinance_history(symbol: str, **history_kwargs) -> pd.DataFrame:
    """yfinance `Ticker.history` with a short retry on empty frames (transient blips)."""
    import yfinance as yf

    symbol = normalize_symbol(symbol)
    last = pd.DataFrame()
    for _ in range(_YF_RETRY_ATTEMPTS):
        last = yf.Ticker(symbol).history(**history_kwargs)
        if not last.empty:
            return last
        time.sleep(_YF_RETRY_SLEEP_S)
    return last


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1d",
    start=None,
    end=None,
) -> pd.DataFrame:
    """Bars with columns Open, High, Low, Close, Volume. `end` is exclusive."""
    symbol = normalize_symbol(symbol)
    if is_bybit_crypto(symbol):
        return fetch_klines(symbol, timeframe, start=start, end=end)
    kwargs: dict = {"auto_adjust": True, "actions": False}
    if start not in (None, ""):
        kwargs["start"] = start
    if end not in (None, ""):
        kwargs["end"] = end
    if timeframe and timeframe != "1d":
        kwargs["interval"] = "1wk" if timeframe == "1w" else timeframe
    return fetch_yfinance_history(symbol, **kwargs)


def fetch_ohlcv_period(symbol: str, timeframe: str, period: str) -> pd.DataFrame:
    """Recent stock bars via yfinance `period=` (signal bot / rolling windows)."""
    symbol = normalize_symbol(symbol)
    if is_bybit_crypto(symbol):
        raise ValueError(f"fetch_ohlcv_period is for yfinance tickers only, not '{symbol}'")
    interval = "1wk" if timeframe == "1w" else timeframe
    return fetch_yfinance_history(
        symbol,
        period=period,
        interval=interval,
        auto_adjust=True,
        actions=False,
    )
