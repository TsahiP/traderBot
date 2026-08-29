"""Shared Alpaca client and market data service.

Provides a single source of truth for Alpaca credentials, clients,
account/position snapshots, and market bar retrieval for both
the live bot (livebot.py) and the API server (dashboard.py).
"""
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

import config

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

logger = logging.getLogger("tradebot")
NY = ZoneInfo("America/New_York")


def get_alpaca_credentials() -> tuple[str, str]:
    """Return (api_key, secret_key) from environment or empty strings."""
    key = os.getenv("ALPACA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_SECRET_KEY", "").strip()
    return key, secret


def has_alpaca_credentials() -> bool:
    key, secret = get_alpaca_credentials()
    return bool(key and secret)


def get_trading_client(paper: bool = True):
    """Instantiate Alpaca TradingClient with paper-trading safety."""
    key, secret = get_alpaca_credentials()
    if not key or not secret:
        raise ValueError("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
    from alpaca.trading.client import TradingClient

    return TradingClient(key, secret, paper=paper)


def get_data_client():
    """Instantiate Alpaca StockHistoricalDataClient."""
    key, secret = get_alpaca_credentials()
    if not key or not secret:
        raise ValueError("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
    from alpaca.data.historical import StockHistoricalDataClient

    return StockHistoricalDataClient(key, secret)


def get_last_completed_bars(data_client, symbol: str | None = None, limit: int | None = None) -> pd.DataFrame:
    """Fetch completed daily bars, dropping the current in-progress bar."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    sym = symbol or config.SYMBOL
    lim = limit or config.ALPACA_BARS_LIMIT

    request = StockBarsRequest(
        symbol_or_symbols=sym,
        timeframe=TimeFrame.Day,
        start=datetime.now(NY) - timedelta(days=500),
        limit=lim,
    )
    response = data_client.get_stock_bars(request)
    df = response.df
    if df.empty:
        return df

    df = df.reset_index().set_index("timestamp")
    # Normalize timestamp index to NY timezone for date comparisons
    if df.index.tz is not None:
        ny_index = df.index.tz_convert(NY)
    else:
        ny_index = df.index.tz_localize("UTC").tz_convert(NY)

    today_ny = datetime.now(NY).date()
    if len(df) > 0 and ny_index[-1].date() == today_ny:
        df = df.iloc[:-1]
    return df


def get_all_positions(trading_client) -> dict[str, dict]:
    """Retrieve all open positions mapped by symbol."""
    positions = {}
    try:
        raw_positions = trading_client.get_all_positions()
        for p in raw_positions:
            sym = str(p.symbol).upper()
            positions[sym] = {
                "symbol": sym,
                "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "current": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_pl_pct": float(p.unrealized_plpc),
            }
    except Exception as exc:
        logger.warning("Error fetching all positions: %s", exc)
    return positions


def get_account_snapshot(trading_client, symbol: str | None = None) -> dict:
    """Retrieve account balances, daily P&L, and open positions."""
    sym = symbol or config.SYMBOL
    account = trading_client.get_account()
    all_positions = get_all_positions(trading_client)
    position_data = all_positions.get(sym)
    equity = float(account.equity)
    last_equity = float(account.last_equity)

    return {
        "account": {
            "equity": equity,
            "last_equity": last_equity,
            "day_pnl": equity - last_equity,
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
        },
        "position": position_data,
        "positions": list(all_positions.values()),
    }


def fetch_fill_price(
    trading_client,
    order_id: str,
    fallback_price: float,
    max_retries: int = 3,
    retry_delay: float = 0.5,
) -> float:
    """Poll for filled_avg_price with brief backoff before using fallback."""
    for attempt in range(max_retries):
        try:
            order = trading_client.get_order_by_id(order_id)
            if order and order.filled_avg_price:
                return float(order.filled_avg_price)
        except Exception as exc:
            logger.debug("Attempt %s fetching fill price for %s failed: %s", attempt + 1, order_id, exc)
        if attempt < max_retries - 1:
            time.sleep(retry_delay)
    return fallback_price
