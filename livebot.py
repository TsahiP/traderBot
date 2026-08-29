"""Paper-trading bot: trades watchlist on Alpaca's paper account using the
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

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

import alpaca_service
import config
import notifier
import telegram_bot
from strategy import compute_signals, latest_signal

NY = ZoneInfo("America/New_York")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tradebot")
_handler = RotatingFileHandler(
    config.LOG_DIR / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
if not logger.handlers:
    logger.addHandler(_handler)


def get_clients():
    if not alpaca_service.has_alpaca_credentials():
        sys.exit(
            "Missing ALPACA_API_KEY / ALPACA_SECRET_KEY in .env - copy .env.example and add your paper keys."
        )
    trading = alpaca_service.get_trading_client(paper=config.ALPACA_PAPER)
    data = alpaca_service.get_data_client()
    return trading, data


def get_last_completed_bars(data, symbol: str) -> pd.DataFrame:
    return alpaca_service.get_last_completed_bars(data, symbol=symbol, limit=config.ALPACA_BARS_LIMIT)


def has_position(trading, symbol: str) -> bool:
    try:
        trading.get_open_position(symbol)
        return True
    except Exception:
        return False


def get_open_position_details(trading, symbol: str) -> dict | None:
    try:
        p = trading.get_open_position(symbol)
        return {
            "qty": float(p.qty),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
        }
    except Exception:
        return None


def place_order(trading, symbol: str, side: str, qty: int | None = None):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    quantity = qty or config.QUANTITY
    order = MarketOrderRequest(
        symbol=symbol,
        qty=quantity,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    submitted = trading.submit_order(order)
    logger.info("Order placed: %s %s x%s (id=%s)", side, symbol, quantity, submitted.id)
    return submitted


def fill_price(trading, order_id: str, fallback: float) -> float:
    return alpaca_service.fetch_fill_price(trading, order_id, fallback)


def notify_order(symbol: str, side: str, price: float, qty: int, pnl: float | None = None) -> None:
    side_label = side.upper()
    msg = f"{side_label} {symbol} x{qty} @ ${price:.2f}"
    if pnl is not None:
        sign = "+" if pnl >= 0 else ""
        msg += f" | P&L: {sign}${pnl:.2f}"
    notifier.send_telegram(msg)


def maybe_send_heartbeat(active_count: int, last_heartbeat: float) -> float:
    if not config.TELEGRAM_ENABLED:
        return last_heartbeat
    now = time.time()
    if now - last_heartbeat < config.TELEGRAM_HEARTBEAT_HOURS * 3600:
        return last_heartbeat
    notifier.send_telegram(f"Bot alive | active positions: {active_count} | watchlist: {len(config.WATCHLIST)} symbols")
    return now


def record_trade(symbol: str, entry: dict, exit_price: float, exit_date: str, qty: int | None = None) -> None:
    """Append one closed round trip to output/live_trades.csv with symbol tracking."""
    path = config.OUTPUT_DIR / "live_trades.csv"
    trade_qty = qty or config.QUANTITY
    entry_p = entry.get("entry_price", exit_price)
    entry_d = entry.get("entry_date", exit_date)
    pnl = round((exit_price - entry_p) * trade_qty, 2)

    is_new = not path.exists()
    has_symbol_col = False
    if not is_new:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                first_line = fh.readline()
                has_symbol_col = "symbol" in first_line
        except Exception:
            has_symbol_col = False

    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(["symbol", "entry_date", "entry_price", "exit_date", "exit_price", "qty", "pnl"])
            writer.writerow([symbol, entry_d, entry_p, exit_date, exit_price, trade_qty, pnl])
        elif has_symbol_col:
            writer.writerow([symbol, entry_d, entry_p, exit_date, exit_price, trade_qty, pnl])
        else:
            # Legacy CSV format fallback
            writer.writerow([entry_d, entry_p, exit_date, exit_price, trade_qty, pnl])

    logger.info("Recorded round trip for %s: entry %.2f -> exit %.2f (P&L $%.2f)", symbol, entry_p, exit_price, pnl)


def run_watchlist_cycle(
    trading,
    data,
    open_trades: dict[str, dict],
    control: telegram_bot.RuntimeControl | None = None,
) -> dict[str, dict]:
    """Execute one scan across all symbols in config.WATCHLIST."""
    for symbol in config.WATCHLIST:
        try:
            bars = get_last_completed_bars(data, symbol)
            if len(bars) < config.SMA_SLOW + 5:
                logger.warning("[%s] Not enough bars yet (%s) - skipping", symbol, len(bars))
                continue

            df = compute_signals(bars, config.SMA_FAST, config.SMA_SLOW)
            signal = latest_signal(df)
            position = has_position(trading, symbol)
            last_close = float(df["Close"].iloc[-1])

            # Synchronize open_trade if position was opened externally or on startup
            if position and symbol not in open_trades:
                pos_details = get_open_position_details(trading, symbol)
                if pos_details:
                    open_trades[symbol] = {
                        "entry_date": datetime.now(NY).isoformat(timespec="seconds"),
                        "entry_price": pos_details["avg_entry_price"],
                        "qty": int(pos_details["qty"]),
                    }

            entries_paused = control is not None and control.is_paused()
            if signal == 1 and not position and entries_paused:
                logger.info("[%s] BUY signal ignored while new entries are paused", symbol)
            elif signal == 1 and not position:
                order = place_order(trading, symbol, "buy", qty=config.QUANTITY)
                entry_price = fill_price(trading, order.id, last_close)
                open_trades[symbol] = {
                    "entry_date": datetime.now(NY).isoformat(timespec="seconds"),
                    "entry_price": entry_price,
                    "qty": config.QUANTITY,
                }
                notify_order(symbol, "buy", entry_price, config.QUANTITY)
                logger.info("[%s] BUY signal on last close %.2f - bought @ %.2f", symbol, last_close, entry_price)
            elif signal == -1 and position:
                trade_info = open_trades.get(symbol, {})
                trade_qty = int(trade_info.get("qty", config.QUANTITY))
                order = place_order(trading, symbol, "sell", qty=trade_qty)
                exit_price = fill_price(trading, order.id, last_close)
                entry_price = trade_info.get("entry_price", exit_price)
                pnl = round((exit_price - entry_price) * trade_qty, 2)
                notify_order(symbol, "sell", exit_price, trade_qty, pnl=pnl)
                record_trade(symbol, trade_info, exit_price, datetime.now(NY).isoformat(timespec="seconds"), qty=trade_qty)
                open_trades.pop(symbol, None)
                logger.info("[%s] SELL signal on last close %.2f - sold @ %.2f (P&L $%.2f)", symbol, last_close, exit_price, pnl)
            else:
                logger.info("[%s] No action (signal=%s, position=%s, close=%.2f)", symbol, signal, position, last_close)
        except Exception as exc:
            logger.exception("[%s] Symbol scan error: %s", symbol, exc)
    return open_trades


def main() -> None:
    trading, data = get_clients()
    control = telegram_bot.RuntimeControl()
    logger.info("Starting paper bot for watchlist %s (SMA %s/%s, qty %s)", config.WATCHLIST, config.SMA_FAST, config.SMA_SLOW, config.QUANTITY)

    # Reconnect to existing positions on startup
    open_trades: dict[str, dict] = {}
    all_positions = alpaca_service.get_all_positions(trading)
    for sym, pos_info in all_positions.items():
        logger.info("Found existing open position on startup: %s x%s @ $%.2f", sym, pos_info["qty"], pos_info["avg_entry"])
        open_trades[sym] = {
            "entry_date": datetime.now(NY).isoformat(timespec="seconds"),
            "entry_price": pos_info["avg_entry"],
            "qty": int(pos_info["qty"]),
        }

    last_heartbeat = 0.0
    if config.TELEGRAM_ENABLED:
        telegram_bot.start_telegram_listener(trading, control)
        notifier.send_telegram(
            f"Paper bot started ACTIVE for watchlist ({len(config.WATCHLIST)} symbols: "
            f"{', '.join(config.WATCHLIST)}) (SMA {config.SMA_FAST}/{config.SMA_SLOW}, "
            f"qty {config.QUANTITY}). Use /help for commands."
        )
        last_heartbeat = time.time()

    while True:
        try:
            clock = trading.get_clock()
            if not clock.is_open:
                logger.info("Market closed - sleeping %s min", config.POLL_INTERVAL_MIN)
                last_heartbeat = maybe_send_heartbeat(len(open_trades), last_heartbeat)
                time.sleep(config.POLL_INTERVAL_MIN * 60)
                continue

            open_trades = run_watchlist_cycle(trading, data, open_trades, control)
            last_heartbeat = maybe_send_heartbeat(len(open_trades), last_heartbeat)
        except Exception as exc:
            logger.exception("Loop error: %s", exc)
            if config.TELEGRAM_ENABLED:
                notifier.send_telegram(f"Bot error: {exc}")

        time.sleep(config.POLL_INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()
