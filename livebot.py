"""Paper-trading bot: trades SPY on Alpaca's paper account using the same
SMA crossover logic as the backtest. Acts on the last completed daily bar.
"""
import csv
import logging
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

import config

load_dotenv(config.BASE_DIR / ".env")

NY = ZoneInfo("America/New_York")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tradebot")
_handler = RotatingFileHandler(
    config.LOG_DIR / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)


def get_clients():
    import os

    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient

    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit(
            "Missing ALPACA_API_KEY / ALPACA_SECRET_KEY in .env - copy .env.example and add your paper keys."
        )
    trading = TradingClient(key, secret, paper=True)
    data = StockHistoricalDataClient(key, secret)
    return trading, data


def get_last_completed_bars(data) -> pd.DataFrame:
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from datetime import datetime, timedelta

    request = StockBarsRequest(
        symbol_or_symbols=config.SYMBOL,
        timeframe=TimeFrame.Day,
        start=datetime.now(NY) - timedelta(days=400),
        limit=config.ALPACA_BARS_LIMIT,
    )
    response = data.get_stock_bars(request)
    df = response.df
    df = df.reset_index().set_index("timestamp")
    today = datetime.now(NY).date()
    if df.index[-1].date() == today:  # drop the in-progress bar
        df = df.iloc[:-1]
    return df


def has_position(trading) -> bool:
    try:
        trading.get_open_position(config.SYMBOL)
        return True
    except Exception:
        return False


def place_order(trading, side: str):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    order = MarketOrderRequest(
        symbol=config.SYMBOL,
        qty=config.QUANTITY,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    submitted = trading.submit_order(order)
    logger.info("Order placed: %s %s x%s (id=%s)", side, config.SYMBOL, config.QUANTITY, submitted.id)
    return submitted


def fill_price(trading, order_id: str, fallback: float) -> float:
    try:
        order = trading.get_order_by_id(order_id)
        if order.filled_avg_price:
            return float(order.filled_avg_price)
    except Exception:
        pass
    return fallback


def record_trade(entry: dict, exit_price: float, exit_date: str) -> None:
    """Append one closed round trip to output/live_trades.csv."""
    path = config.OUTPUT_DIR / "live_trades.csv"
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(["entry_date", "entry_price", "exit_date", "exit_price", "qty", "pnl"])
        writer.writerow([
            entry["entry_date"], entry["entry_price"], exit_date, exit_price,
            config.QUANTITY, round((exit_price - entry["entry_price"]) * config.QUANTITY, 2),
        ])
    logger.info("Recorded round trip: entry %.2f -> exit %.2f", entry["entry_price"], exit_price)


def main() -> None:
    trading, data = get_clients()
    logger.info("Starting paper bot for %s (SMA %s/%s, qty %s)", config.SYMBOL, config.SMA_FAST, config.SMA_SLOW, config.QUANTITY)
    open_trade: dict = {}

    while True:
        from strategy import compute_signals, latest_signal

        try:
            clock = trading.get_clock()
            if not clock.is_open:
                logger.info("Market closed - sleeping %s min", config.POLL_INTERVAL_MIN)
                time.sleep(config.POLL_INTERVAL_MIN * 60)
                continue

            bars = get_last_completed_bars(data)
            if len(bars) < config.SMA_SLOW + 5:
                logger.warning("Not enough bars yet (%s) - sleeping", len(bars))
                time.sleep(config.POLL_INTERVAL_MIN * 60)
                continue

            df = compute_signals(bars, config.SMA_FAST, config.SMA_SLOW)
            signal = latest_signal(df)
            position = has_position(trading)
            last_close = df["Close"].iloc[-1]

            if signal == 1 and not position:
                order = place_order(trading, "buy")
                entry_price = fill_price(trading, order.id, last_close)
                open_trade = {
                    "entry_date": datetime.now(NY).isoformat(timespec="seconds"),
                    "entry_price": entry_price,
                }
                logger.info("BUY signal on last close %.2f - bought @ %.2f", last_close, entry_price)
            elif signal == -1 and position:
                order = place_order(trading, "sell")
                exit_price = fill_price(trading, order.id, last_close)
                record_trade(open_trade, exit_price, datetime.now(NY).isoformat(timespec="seconds"))
                open_trade = {}
                logger.info("SELL signal on last close %.2f - sold @ %.2f", last_close, exit_price)
            else:
                logger.info("No action (signal=%s, position=%s, close=%.2f)", signal, position, last_close)
        except Exception as exc:
            logger.exception("Loop error: %s", exc)

        time.sleep(config.POLL_INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()