"""Paper-trading bot: trades SPY on Alpaca's paper account using the same
SMA crossover logic as the backtest. Acts on the last completed daily bar.

Every entry is a bracket order (market buy + GTC stop-loss + GTC take-profit
held at Alpaca) and must pass the risk gate in `risk.py` first. The open
trade and today's entries live in `output/livebot_state.json`, so a restart
neither loses the entry price nor re-buys the same crossover. A stop or
target filled at the broker is picked up on the next cycle and recorded.
"""
import csv
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

import config
import risk

load_dotenv(config.BASE_DIR / ".env")

NY = ZoneInfo("America/New_York")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tradebot")
_handler = RotatingFileHandler(
    config.LOG_DIR / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)

STATE_PATH = config.LIVEBOT_STATE_PATH
TRADES_PATH = config.OUTPUT_DIR / "live_trades.csv"
STATE_KEEP_DAYS = 7          # entries older than this drop out of the state file
FILL_WAIT_TRIES = 10         # x FILL_WAIT_S seconds to wait for a market fill
FILL_WAIT_S = 1.0
PENDING_STATUSES = {"new", "accepted", "pending_new", "accepted_for_bidding", "partially_filled", "held"}
DEAD_STATUSES = {"canceled", "expired", "rejected", "done_for_day", "stopped", "suspended"}
_sleep = time.sleep          # tests swap this out

BAR_COLUMNS = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}


def _val(x) -> str:
    """Enum or plain value -> lowercase string (alpaca-py returns enums)."""
    return str(getattr(x, "value", x) or "").lower()


def get_clients():
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


# ---------------------------------------------------------------- state

def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            state = json.load(fh)
    except FileNotFoundError:
        state = {}
    except (OSError, ValueError) as exc:
        logger.error("State file unreadable (%s) - starting empty", exc)
        state = {}
    state.setdefault("open_trade", None)
    state.setdefault("entries", [])
    state.setdefault("acted_signal_bar", None)
    state.setdefault("last_block", None)
    return state


def save_state(state: dict, now: datetime) -> None:
    cutoff = (now - timedelta(days=STATE_KEEP_DAYS)).date().isoformat()
    state["entries"] = [e for e in state["entries"] if e.get("date", "") >= cutoff]
    tmp = STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, STATE_PATH)


def entries_today(state: dict, now: datetime) -> list[dict]:
    today = now.date().isoformat()
    return [e for e in state["entries"] if e.get("date") == today]


# ---------------------------------------------------------------- market data

def bars_from_alpaca(raw: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Alpaca BarSet.df -> OHLCV with title-case columns and a NY index,
    the in-progress daily bar dropped."""
    df = raw
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(config.SYMBOL, level="symbol")
    df = df.rename(columns=BAR_COLUMNS)[list(BAR_COLUMNS.values())]
    idx = pd.DatetimeIndex(df.index)
    df.index = idx.tz_localize("UTC").tz_convert(NY) if idx.tz is None else idx.tz_convert(NY)
    if len(df) and df.index[-1].date() == now.date():
        df = df.iloc[:-1]
    return df


def get_last_completed_bars(data, now: datetime) -> pd.DataFrame:
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    request = StockBarsRequest(
        symbol_or_symbols=config.SYMBOL,
        timeframe=TimeFrame.Day,
        start=now - timedelta(days=400),
        end=now - timedelta(minutes=16),  # free plan: no SIP data from the last 15 min
        limit=config.ALPACA_BARS_LIMIT,
    )
    return bars_from_alpaca(data.get_stock_bars(request).df, now)


def latest_price(data, fallback: float) -> float:
    """Last IEX trade (free feed); the last daily close when that fails."""
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestTradeRequest

        req = StockLatestTradeRequest(symbol_or_symbols=config.SYMBOL, feed=DataFeed.IEX)
        trade = data.get_stock_latest_trade(req)[config.SYMBOL]
        if trade.price and trade.price > 0:
            return float(trade.price)
    except Exception as exc:
        logger.warning("Latest trade unavailable (%s) - using last close", exc)
    return float(fallback)


def atr(df: pd.DataFrame, period: int = config.ATR_PERIOD) -> float | None:
    if len(df) < period + 1:
        return None
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    val = float(tr.rolling(period).mean().iloc[-1])
    return val if math.isfinite(val) and val > 0 else None


def signal_bar_key(df: pd.DataFrame) -> str | None:
    """Date of the bar that produced the latest crossover (acted on once)."""
    nonzero = df.loc[df["signal"] != 0]
    return None if nonzero.empty else str(nonzero.index[-1].date())


def bracket_levels(ref: float, atr_value: float) -> tuple[float, float]:
    stop = round(ref - config.STOP_ATR_MULT * atr_value, 2)
    target = round(ref + config.TARGET_R * (ref - stop), 2)
    return stop, target


# ---------------------------------------------------------------- broker

def get_position(trading):
    """The open SYMBOL position or None. API errors propagate, so a network
    blip aborts the cycle instead of looking like 'no position'."""
    for pos in trading.get_all_positions():
        if pos.symbol == config.SYMBOL:
            return pos
    return None


def wait_for_fill(trading, order_id, fallback: float) -> float:
    for _ in range(FILL_WAIT_TRIES):
        try:
            order = trading.get_order_by_id(order_id)
            if order.filled_avg_price:
                return float(order.filled_avg_price)
        except Exception as exc:
            logger.warning("Fill lookup failed for %s: %s", order_id, exc)
        _sleep(FILL_WAIT_S)
    logger.warning("No fill price for %s yet - recording %.2f", order_id, fallback)
    return float(fallback)


def place_bracket_buy(trading, qty: int, stop: float, target: float):
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

    order = MarketOrderRequest(
        symbol=config.SYMBOL,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC,  # the legs must outlive the day (swing trade)
        order_class=OrderClass.BRACKET,
        stop_loss=StopLossRequest(stop_price=stop),
        take_profit=TakeProfitRequest(limit_price=target),
    )
    submitted = trading.submit_order(order)
    logger.info("Bracket buy placed: %s x%s stop %.2f target %.2f (id=%s)",
                config.SYMBOL, qty, stop, target, submitted.id)
    return submitted


def cancel_symbol_orders(trading) -> None:
    """Cancel the open bracket legs so their held shares can be sold."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[config.SYMBOL])
    for order in trading.get_orders(filter=req):
        trading.cancel_order_by_id(order.id)
        logger.info("Canceled open order %s (%s)", order.id, _val(order.type))
    for _ in range(FILL_WAIT_TRIES):
        if not trading.get_orders(filter=req):
            return
        _sleep(FILL_WAIT_S)
    logger.warning("Open orders for %s still pending cancel - selling anyway", config.SYMBOL)


def place_market_sell(trading, qty: float):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    order = MarketOrderRequest(symbol=config.SYMBOL, qty=qty, side=OrderSide.SELL,
                               time_in_force=TimeInForce.DAY)
    submitted = trading.submit_order(order)
    logger.info("Order placed: sell %s x%s (id=%s)", config.SYMBOL, qty, submitted.id)
    return submitted


def filled_exit_leg(trading, order_id) -> tuple[float, str] | None:
    """(price, 'stop' | 'target') of the bracket leg that closed the trade."""
    from alpaca.trading.requests import GetOrderByIdRequest

    parent = trading.get_order_by_id(order_id, filter=GetOrderByIdRequest(nested=True))
    for leg in parent.legs or []:
        if _val(leg.status) == "filled" and leg.filled_avg_price:
            kind = "target" if _val(leg.type) == "limit" else "stop"
            return float(leg.filled_avg_price), kind
    return None


def record_trade(entry: dict, exit_price: float, exit_date: str, qty: float, exit_type: str) -> None:
    """Append one closed round trip to output/live_trades.csv."""
    is_new = not TRADES_PATH.exists()
    pnl = round((exit_price - entry["entry_price"]) * qty, 2)
    with open(TRADES_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(["entry_date", "entry_price", "exit_date", "exit_price", "qty", "pnl"])
        writer.writerow([entry.get("entry_date") or exit_date, entry["entry_price"], exit_date,
                         exit_price, qty, pnl])
    logger.info("Recorded round trip (%s): entry %.2f -> exit %.2f x%s, P&L %.2f",
                exit_type, entry["entry_price"], exit_price, qty, pnl)


# ---------------------------------------------------------------- one cycle

def reconcile(trading, state: dict, position, last_close: float, now: datetime) -> None:
    """Line the state up with the broker before acting on a signal."""
    trade = state["open_trade"]
    if position is not None and trade is None:
        state["open_trade"] = {
            "entry_date": None,
            "entry_price": float(position.avg_entry_price),
            "qty": float(position.qty),
            "stop": None, "target": None, "order_id": None,
        }
        logger.warning("Adopted an untracked %s position: %s @ %.2f (no bracket known)",
                       config.SYMBOL, position.qty, float(position.avg_entry_price))
        return
    if position is not None or trade is None:
        return

    # A tracked trade with no position: still entering, never filled, or the broker closed it.
    exit_info = None
    if trade.get("order_id"):
        from alpaca.trading.requests import GetOrderByIdRequest

        parent = trading.get_order_by_id(trade["order_id"], filter=GetOrderByIdRequest(nested=True))
        status = _val(parent.status)
        if status in PENDING_STATUSES:
            logger.info("Entry order %s still %s - waiting", trade["order_id"], status)
            return
        if status in DEAD_STATUSES and not float(parent.filled_qty or 0):
            logger.warning("Entry order %s ended %s without a fill - clearing", trade["order_id"], status)
            state["open_trade"] = None
            return
        exit_info = filled_exit_leg(trading, trade["order_id"])
    price, kind = exit_info if exit_info else (last_close, "broker")
    if not exit_info:
        logger.warning("Position closed outside the bot - recording at last close %.2f", last_close)
    record_trade(trade, price, now.isoformat(timespec="seconds"), trade["qty"], kind)
    state["open_trade"] = None


def try_enter(trading, data, state: dict, df: pd.DataFrame, signal_bar: str, now: datetime) -> None:
    last_close = float(df["Close"].iloc[-1])
    atr_value = atr(df)
    if atr_value is None:
        logger.warning("No ATR yet - cannot place a stop, skipping the entry")
        return
    ref = latest_price(data, last_close)
    stop, target = bracket_levels(ref, atr_value)
    account = trading.get_account()
    verdict = risk.check_entry(
        symbol=config.SYMBOL, qty=config.QUANTITY, entry=ref, stop=stop, target=target,
        now_ny=now, equity=float(account.equity), cash=float(account.cash),
        entries_today=entries_today(state, now), limits=risk_limits(),
    )
    if not verdict.ok:
        summary = verdict.summary()
        if summary != state.get("last_block"):
            logger.warning("BUY signal (%s) %s", signal_bar, summary)
            state["last_block"] = summary
        return

    order = place_bracket_buy(trading, config.QUANTITY, stop, target)
    entry_price = wait_for_fill(trading, order.id, ref)
    state["open_trade"] = {
        "entry_date": now.isoformat(timespec="seconds"),
        "entry_price": entry_price,
        "qty": config.QUANTITY,
        "stop": stop, "target": target,
        "order_id": str(order.id),
    }
    state["entries"].append({
        "date": now.date().isoformat(), "ts": now.isoformat(timespec="seconds"),
        "symbol": config.SYMBOL, "max_loss": risk.max_loss(config.QUANTITY, ref, stop),
        "order_id": str(order.id),
    })
    state["acted_signal_bar"] = signal_bar
    state["last_block"] = None
    logger.info("BUY signal (%s) - bought @ %.2f, stop %.2f, target %.2f, risk PASS",
                signal_bar, entry_price, stop, target)


def exit_position(trading, state: dict, position, last_close: float, now: datetime) -> None:
    """Signal exit - never risk-gated."""
    cancel_symbol_orders(trading)
    qty = float(position.qty)
    order = place_market_sell(trading, qty)
    exit_price = wait_for_fill(trading, order.id, last_close)
    trade = state["open_trade"] or {"entry_date": None, "entry_price": float(position.avg_entry_price)}
    record_trade(trade, exit_price, now.isoformat(timespec="seconds"), qty, "signal")
    state["open_trade"] = None
    logger.info("SELL signal - sold @ %.2f", exit_price)


def risk_limits() -> risk.RiskLimits:
    return risk.RiskLimits(
        max_loss_per_trade_usd=config.RISK_MAX_LOSS_PER_TRADE_USD,
        max_daily_loss_usd=config.RISK_MAX_DAILY_LOSS_USD,
        max_trades_per_day=config.RISK_MAX_TRADES_PER_DAY,
        min_reward_risk=config.RISK_MIN_REWARD_RISK,
        max_position_pct=config.RISK_MAX_POSITION_PCT,
        cash_buffer_pct=config.RISK_CASH_BUFFER_PCT,
        no_entries_before=config.RISK_NO_ENTRIES_BEFORE,
        no_entries_after=config.RISK_NO_ENTRIES_AFTER,
    )


def run_cycle(trading, data, state: dict, now: datetime) -> str:
    """One poll. Returns a short status for the log/tests."""
    from strategy import compute_signals, latest_signal

    if not trading.get_clock().is_open:
        return "closed"
    bars = get_last_completed_bars(data, now)
    if len(bars) < config.SMA_SLOW + 5:
        logger.warning("Not enough bars yet (%s) - sleeping", len(bars))
        return "warmup"

    df = compute_signals(bars, config.SMA_FAST, config.SMA_SLOW)
    signal = latest_signal(df)
    signal_bar = signal_bar_key(df)
    last_close = float(df["Close"].iloc[-1])

    position = get_position(trading)
    reconcile(trading, state, position, last_close, now)

    status = "idle"
    if signal == 1 and position is None and state["open_trade"] is None:
        if state.get("acted_signal_bar") == signal_bar:
            status = "already_acted"
        else:
            try_enter(trading, data, state, df, signal_bar, now)
            status = "entered" if state["open_trade"] else "blocked"
    elif signal == -1 and position is not None:
        exit_position(trading, state, position, last_close, now)
        status = "exited"
    if status in ("idle", "already_acted"):
        logger.info("No action (signal=%s, position=%s, close=%.2f)", signal, position is not None, last_close)
    save_state(state, now)
    return status


def main() -> None:
    trading, data = get_clients()
    logger.info("Starting paper bot for %s (SMA %s/%s, qty %s, stop %sx ATR, target %sR)",
                config.SYMBOL, config.SMA_FAST, config.SMA_SLOW, config.QUANTITY,
                config.STOP_ATR_MULT, config.TARGET_R)
    state = load_state()

    while True:
        try:
            if run_cycle(trading, data, state, datetime.now(NY)) == "closed":
                logger.info("Market closed - sleeping %s min", config.POLL_INTERVAL_MIN)
        except Exception as exc:
            logger.exception("Loop error: %s", exc)
        time.sleep(config.POLL_INTERVAL_MIN * 60)


if __name__ == "__main__":
    main()
