"""Bybit v5 spot klines. Market data only — this module never places orders.

Public candles are requested with the signed headers so a bad key fails
loudly. Secrets are read from `.env` and are not logged.
"""
import hashlib
import hmac
import os
import time
from datetime import date, datetime, timezone
from urllib.parse import urlencode

import pandas as pd
import requests
from dotenv import load_dotenv

import config

_BASE = "https://api.bybit.com"
_KLINE_PATH = "/v5/market/kline"
_RECV_WINDOW = "5000"
_KLINE_LIMIT = 1000
_MAX_PAGES = 40

# interval code, candle length in milliseconds
_INTERVALS: dict[str, tuple[str, int]] = {
    "1m": ("1", 60_000),
    "5m": ("5", 5 * 60_000),
    "15m": ("15", 15 * 60_000),
    "30m": ("30", 30 * 60_000),
    "1h": ("60", 60 * 60_000),
    "1d": ("D", 86_400_000),
    "1w": ("W", 7 * 86_400_000),
}

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]


class BybitError(RuntimeError):
    pass


def _keys() -> tuple[str, str]:
    load_dotenv(config.BASE_DIR / ".env")
    key = (os.getenv("BYBIT_API_KEY") or "").strip()
    secret = (os.getenv("BYBIT_SECRET_KEY") or "").strip()
    if not key or not secret:
        raise BybitError(
            "Missing BYBIT_API_KEY / BYBIT_SECRET_KEY in .env — "
            "both are required to fetch -USD crypto bars from Bybit."
        )
    return key, secret


def _query(params: dict) -> str:
    items = [(k, str(params[k])) for k in sorted(params) if params[k] is not None]
    return urlencode(items)


def _sign(secret: str, timestamp: str, api_key: str, query: str) -> str:
    payload = f"{timestamp}{api_key}{_RECV_WINDOW}{query}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _to_ms(value, *, exclusive: bool = False) -> int:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    else:
        text = str(value).strip()
        if len(text) == 10:
            parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dt = parsed
        else:
            ts = pd.Timestamp(text)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            dt = ts.to_pydatetime()
    ms = int(dt.timestamp() * 1000)
    return ms - 1 if exclusive else ms


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=_OHLCV)


def _get_kline(params: dict) -> dict:
    key, secret = _keys()
    query = _query(params)
    timestamp = str(int(time.time() * 1000))
    headers = {
        "X-BAPI-API-KEY": key,
        "X-BAPI-SIGN": _sign(secret, timestamp, key, query),
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
    }
    url = f"{_BASE}{_KLINE_PATH}?{query}"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        raise BybitError(f"Bybit request failed: {exc}") from exc
    except ValueError as exc:
        raise BybitError("Bybit returned a non-JSON response") from exc
    if body.get("retCode") != 0:
        raise BybitError(body.get("retMsg") or "Bybit kline request failed")
    return body


def fetch_klines(
    symbol: str,
    timeframe: str = "1d",
    start=None,
    end=None,
) -> pd.DataFrame:
    """Spot OHLCV, oldest first. `end` is exclusive (same as yfinance).

    The forming candle is dropped: Bybit includes it, and its close time
    is still in the future.
    """
    from market_symbols import to_bybit_spot_symbol

    if timeframe not in _INTERVALS:
        raise BybitError(f"Unsupported Bybit timeframe '{timeframe}'")
    interval, interval_ms = _INTERVALS[timeframe]
    spot = to_bybit_spot_symbol(symbol)
    now_ms = int(time.time() * 1000)
    start_ms = _to_ms(start) if start not in (None, "") else None
    end_ms = now_ms if end in (None, "") else _to_ms(end, exclusive=True)
    if start_ms is not None and start_ms > end_ms:
        return _empty()

    rows: list = []
    cursor = end_ms
    prev_oldest: int | None = None
    for _ in range(_MAX_PAGES):
        params: dict = {
            "category": "spot",
            "symbol": spot,
            "interval": interval,
            "end": cursor,
            "limit": _KLINE_LIMIT,
        }
        if start_ms is not None:
            params["start"] = start_ms
        batch = (_get_kline(params).get("result") or {}).get("list") or []
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(item[0]) for item in batch)
        if len(batch) < _KLINE_LIMIT or (start_ms is not None and oldest <= start_ms):
            break
        if prev_oldest is not None and oldest >= prev_oldest:
            break
        prev_oldest = oldest
        cursor = oldest - 1

    parsed: list[tuple[int, float, float, float, float, float]] = []
    seen: set[int] = set()
    for item in rows:
        ts = int(item[0])
        if ts in seen or ts + interval_ms > now_ms:
            continue
        if start_ms is not None and ts < start_ms:
            continue
        if ts > end_ms:
            continue
        seen.add(ts)
        parsed.append(
            (ts, float(item[1]), float(item[2]), float(item[3]), float(item[4]), float(item[5]))
        )
    if not parsed:
        return _empty()

    parsed.sort()
    index = pd.to_datetime([row[0] for row in parsed], unit="ms", utc=True)
    frame = pd.DataFrame(
        {
            "Open": [row[1] for row in parsed],
            "High": [row[2] for row in parsed],
            "Low": [row[3] for row in parsed],
            "Close": [row[4] for row in parsed],
            "Volume": [row[5] for row in parsed],
        },
        index=index,
    )
    frame.index.name = "Date"
    return frame
