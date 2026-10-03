"""Bybit v5 public market data (spot klines)."""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import requests

from market_symbols import to_bybit_spot_symbol

BYBIT_API_BASE = "https://api.bybit.com"
_KLINE_LIMIT = 1000
_RETRY_ATTEMPTS = 3
_RETRY_SLEEP = 0.8

# App timeframe -> Bybit interval string
_INTERVAL_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "1d": "D",
    "1w": "W",
}


def keys_configured() -> bool:
    return bool(os.getenv("BYBIT_API_KEY") and os.getenv("BYBIT_SECRET_KEY"))


def _get(path: str, params: dict[str, Any]) -> dict:
    url = f"{BYBIT_API_BASE}{path}"
    last_exc: Exception | None = None
    for _ in range(_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            body = resp.json()
            if body.get("retCode") != 0:
                raise RuntimeError(body.get("retMsg") or f"Bybit error {body.get('retCode')}")
            return body
        except Exception as exc:
            last_exc = exc
            time.sleep(_RETRY_SLEEP)
    raise last_exc or RuntimeError("Bybit request failed")


def bybit_health() -> dict:
    """Reachability + whether API keys are present in .env."""
    out = {"connected": False, "keys_configured": keys_configured(), "reason": None}
    try:
        _get(
            "/v5/market/kline",
            {
                "category": "spot",
                "symbol": "BTCUSDT",
                "interval": "D",
                "limit": 1,
            },
        )
        out["connected"] = True
    except Exception as exc:
        out["reason"] = str(exc)
    return out


def _parse_kline_rows(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    # Bybit returns newest first; we want ascending time.
    records = []
    for row in reversed(rows):
        ts_ms = int(row[0])
        records.append(
            {
                "Open": float(row[1]),
                "High": float(row[2]),
                "Low": float(row[3]),
                "Close": float(row[4]),
                "Volume": float(row[5]),
                "time": pd.to_datetime(ts_ms, unit="ms"),
            }
        )
    df = pd.DataFrame(records).set_index("time")
    return df


def _fetch_kline_page(
    bybit_symbol: str,
    interval: str,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = _KLINE_LIMIT,
) -> pd.DataFrame:
    params: dict[str, Any] = {
        "category": "spot",
        "symbol": bybit_symbol,
        "interval": interval,
        "limit": min(limit, _KLINE_LIMIT),
    }
    if start_ms is not None:
        params["start"] = start_ms
    if end_ms is not None:
        params["end"] = end_ms
    body = _get("/v5/market/kline", params)
    rows = (body.get("result") or {}).get("list") or []
    return _parse_kline_rows(rows)


def fetch_klines(
    symbol: str,
    timeframe: str,
    *,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
) -> pd.DataFrame:
    """OHLCV for a -USD symbol over [start, end) (end exclusive for date-only ends)."""
    bybit_symbol = to_bybit_spot_symbol(symbol)
    interval = _INTERVAL_MAP.get(timeframe)
    if not interval:
        raise ValueError(f"Unsupported timeframe for Bybit: '{timeframe}'")

    now = datetime.utcnow()
    end_dt = end
    if isinstance(end_dt, date) and not isinstance(end_dt, datetime):
        end_dt = datetime.combine(end_dt, datetime.min.time()) + timedelta(days=1)
    elif end_dt is None:
        end_dt = now
    start_dt = start
    if isinstance(start_dt, date) and not isinstance(start_dt, datetime):
        start_dt = datetime.combine(start_dt, datetime.min.time())
    elif start_dt is None:
        start_dt = end_dt - timedelta(days=365 * 3)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    if start_ms >= end_ms:
        return pd.DataFrame()

    chunks: list[pd.DataFrame] = []
    cursor_end = end_ms
    while cursor_end > start_ms:
        chunk = _fetch_kline_page(
            bybit_symbol,
            interval,
            start_ms=start_ms,
            end_ms=cursor_end,
            limit=_KLINE_LIMIT,
        )
        if chunk.empty:
            break
        chunks.append(chunk)
        oldest_ms = int(chunk.index[0].timestamp() * 1000)
        if oldest_ms <= start_ms:
            break
        cursor_end = oldest_ms - 1
        if len(chunk) < _KLINE_LIMIT:
            break

    if not chunks:
        return pd.DataFrame()
    df = pd.concat(chunks).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    mask = (df.index >= pd.Timestamp(start_dt)) & (df.index < pd.Timestamp(end_dt))
    return df.loc[mask]


def fetch_recent_klines(symbol: str, timeframe: str, lookback_days: int) -> pd.DataFrame:
    """Recent bars for signal scans (no in-progress bar drop — caller handles that)."""
    end = datetime.utcnow()
    start = end - timedelta(days=max(lookback_days, 1))
    return fetch_klines(symbol, timeframe, start=start, end=end)
