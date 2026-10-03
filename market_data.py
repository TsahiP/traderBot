"""Route OHLCV requests to Bybit (-USD crypto) or yfinance (equities)."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import config
from market_symbols import is_crypto_symbol
from bybit_client import fetch_klines, fetch_recent_klines

NY = ZoneInfo("America/New_York")

# Signal bot lookback (calendar days) — mirrors signalbot.TF_PERIOD intent.
_SIGNAL_LOOKBACK_DAYS = {
    "1m": 5,
    "5m": 15,
    "15m": 30,
    "30m": 60,
    "1h": 90,
    "1d": 180,
    "1w": 365 * 5,
}

_YF_INTERVAL = {"1w": "1wk"}
_TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()


def _fetch_yfinance(symbol: str, kwargs: dict) -> pd.DataFrame:
    import yfinance as yf

    last_df = pd.DataFrame()
    for _ in range(3):
        df = yf.Ticker(symbol).history(**kwargs)
        last_df = df
        if not df.empty:
            if df.index.tz is not None:
                df = df.copy()
                df.index = df.index.tz_localize(None)
            return df
        time.sleep(0.8)
    return last_df


def _kwargs_to_bybit_range(kwargs: dict) -> tuple[str, date | datetime | None, date | datetime | None]:
    interval = kwargs.get("interval")
    if interval:
        timeframe = str(interval)
        start_s = kwargs.get("start")
        end_s = kwargs.get("end")
        start = _parse_iso_date(start_s) if start_s else None
        end = _parse_iso_date(end_s) if end_s else None
        return timeframe, start, end
    start_s = kwargs.get("start")
    end_s = kwargs.get("end")
    start = _parse_iso_date(start_s) if start_s else date.fromisoformat(config.BACKTEST_START)
    end = _parse_iso_date(end_s) if end_s else None
    return "1d", start, end


def fetch_history(symbol: str, kwargs: dict) -> pd.DataFrame:
    """Same kwargs shape as yfinance Ticker.history (start/end/interval/auto_adjust)."""
    sym = symbol.strip().upper()
    if is_crypto_symbol(sym):
        timeframe, start, end = _kwargs_to_bybit_range(kwargs)
        return fetch_klines(sym, timeframe, start=start, end=end)
    return _fetch_yfinance(sym, kwargs)


def _drop_in_progress_bar(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if df.empty:
        return df
    now = datetime.now(NY)
    last_ts = df.index[-1].to_pydatetime()
    if timeframe == "1w":
        in_progress = last_ts.isocalendar()[:2] == now.isocalendar()[:2]
    elif timeframe == "1d":
        in_progress = last_ts.date() == now.date()
    else:
        minutes = _TF_MINUTES[timeframe]
        bucket_start = (
            now
            - timedelta(
                minutes=now.minute % minutes,
                seconds=now.second,
                microseconds=now.microsecond,
            )
        ).replace(microsecond=0)
        if minutes >= 60:
            bucket_start = now.replace(minute=0, second=0, microsecond=0)
        in_progress = last_ts >= bucket_start
    if in_progress:
        df = df.iloc[:-1]
    return df


def fetch_recent_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    """Recent OHLCV with the in-progress bar dropped (signal bot)."""
    sym = symbol.strip().upper()
    if is_crypto_symbol(sym):
        days = _SIGNAL_LOOKBACK_DAYS.get(timeframe, 180)
        df = fetch_recent_klines(sym, timeframe, days)
        return _drop_in_progress_bar(df, timeframe)

    import yfinance as yf

    period_map = {
        "1m": "5d",
        "5m": "15d",
        "15m": "30d",
        "30m": "60d",
        "1h": "90d",
        "1d": "180d",
        "1w": "5y",
    }
    yf_interval = _YF_INTERVAL.get(timeframe, timeframe)
    period = period_map.get(timeframe, "180d")
    last_df = pd.DataFrame()
    for _ in range(3):
        df = yf.Ticker(sym).history(
            period=period, interval=yf_interval, auto_adjust=True
        )
        last_df = df
        if not df.empty:
            if df.index.tz is not None:
                df = df.copy()
                df.index = df.index.tz_localize(None)
            return _drop_in_progress_bar(df, timeframe)
        time.sleep(0.8)
    return last_df
