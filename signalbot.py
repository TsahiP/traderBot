"""Signal bot: watches configured symbols/timeframes for Japanese candlestick
patterns and pushes alerts (text + chart image) to Telegram.

Run in its own terminal:  python signalbot.py
Config lives in output/signal_config.json and is re-read every cycle, so edits
made in the Signals tab of the web UI apply without a restart.
"""
import logging
import sys
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

import config
import signals
from candle_patterns import PATTERNS, detect_all
from chart_image import render_candles

load_dotenv(config.BASE_DIR / ".env")

NY = ZoneInfo("America/New_York")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("signalbot")
_handler = RotatingFileHandler(
    config.LOG_DIR / "signalbot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)

# How much history to pull per cycle: enough for 3-bar patterns + a 30-bar chart.
TF_PERIOD = {"1m": "5d", "5m": "15d", "15m": "30d", "30m": "60d", "1h": "90d", "1d": "180d"}
_TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def fetch_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    """Recent OHLCV bars with the in-progress bar dropped."""
    logger.info("hello-> %s", symbol)
    import yfinance as yf

    last_df = pd.DataFrame()
    for _ in range(3):
        df = yf.Ticker(symbol).history(period=TF_PERIOD[timeframe], interval=timeframe, auto_adjust=True)
        last_df = df
        if not df.empty:
            break
        time.sleep(0.8)

    if df.empty:
        return df

    now = datetime.now(NY)
    last_ts = df.index[-1].to_pydatetime()
    if timeframe == "1d":
        in_progress = last_ts.date() == now.date()
    else:
        minutes = _TF_MINUTES[timeframe]
        bucket_start = (now - timedelta(minutes=now.minute % minutes, seconds=now.second, microseconds=now.microsecond)).replace(microsecond=0)
        if minutes >= 60:
            bucket_start = now.replace(minute=0, second=0, microsecond=0)
        in_progress = last_ts >= bucket_start
    if in_progress:
        df = df.iloc[:-1]
    return df


def build_levels(df: pd.DataFrame, pattern_id: str) -> dict:
    """Entry/stop/target for the alert. Stop = extreme of the whole pattern,
    target = 2x the risk (R:R 1:2)."""
    meta = PATTERNS[pattern_id]
    window = df.iloc[-meta["bars"]:]
    close = float(df["Close"].iloc[-1])
    if meta["direction"] == "long":
        stop = float(window["Low"].min())
        risk = close - stop
        target = close + 2 * risk if risk > 0 else None
    else:
        stop = float(window["High"].max())
        risk = stop - close
        target = close - 2 * risk if risk > 0 else None
    return {"entry": close, "stop": round(stop, 4), "target": (round(target, 4) if target is not None else None)}


def build_message(symbol: str, timeframe: str, pattern_id: str, bar_ts: str, levels: dict) -> str:
    meta = PATTERNS[pattern_id]
    side = "LONG" if meta["direction"] == "long" else "SHORT"
    lines = [
        f"{symbol} · {timeframe} — {meta['label']} ({side})",
        f"Close: ${levels['entry']:.2f} · {bar_ts}",
        f"Entry: ~${levels['entry']:.2f}",
        f"Stop: ${levels['stop']:.2f} (pattern extreme)",
    ]
    if levels["target"] is not None:
        lines.append(f"Target: ${levels['target']:.2f} (R:R 1:2)")
    return "\n".join(lines)


def main() -> None:
    logger.info("Starting signal bot")
    while True:
        cycle_start = time.time()
        cfg = signals.load_config()
        signals.write_heartbeat(cfg["poll_minutes"])
        token, _ = signals.telegram_credentials()
        chat_ids = signals.telegram_chat_ids()

        if not (token and chat_ids):
            logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env - skipping cycle")
        else:
            sent = signals.sent_keys()
            bars_cache: dict[tuple[str, str], pd.DataFrame] = {}

            def get_bars(symbol: str, timeframe: str) -> pd.DataFrame | None:
                pair = (symbol, timeframe)
                if pair not in bars_cache:
                    try:
                        df = fetch_bars(symbol, timeframe)
                    except Exception as exc:
                        logger.warning("fetch %s %s failed: %s", symbol, timeframe, exc)
                        df = pd.DataFrame()
                    if len(df) < 5:
                        logger.info("%s %s: not enough bars yet (%d)", symbol, timeframe, len(df))
                        df = pd.DataFrame()
                    bars_cache[pair] = df
                cached = bars_cache[pair]
                return cached if len(cached) else None

            for watchlist in cfg["lists"]:
                list_name = watchlist["name"]
                for symbol in watchlist["symbols"]:
                    for timeframe in watchlist["timeframes"]:
                        df = get_bars(symbol, timeframe)
                        if df is None:
                            continue

                        found = detect_all(df, watchlist["patterns"])
                        bar_ts = str(df.index[-1])[:16].replace("T", " ")
                        for pattern_id in found:
                            key = signals.signal_key(symbol, timeframe, bar_ts, pattern_id)
                            if key in sent:
                                continue

                            levels = build_levels(df, pattern_id)
                            message = build_message(symbol, timeframe, pattern_id, bar_ts, levels)
                            try:
                                photo = render_candles(df, symbol, timeframe, PATTERNS[pattern_id]["label"], PATTERNS[pattern_id]["direction"])
                            except Exception as exc:
                                logger.warning("chart render failed for %s: %s", key, exc)
                                photo = None

                            ok = signals.tg_send(token, chat_ids, text=message, photo=photo)
                            if ok:
                                entry = {
                                    "key": key,
                                    "ts": datetime.now(NY).isoformat(timespec="seconds"),
                                    "list": list_name,
                                    "symbol": symbol,
                                    "timeframe": timeframe,
                                    "pattern_id": pattern_id,
                                    "label": PATTERNS[pattern_id]["label"],
                                    "direction": PATTERNS[pattern_id]["direction"],
                                    "close": levels["entry"],
                                    "entry": levels["entry"],
                                    "stop": levels["stop"],
                                    "target": levels["target"],
                                    "bar_ts": bar_ts,
                                }
                                signals.append_signal(entry)
                                sent.add(key)
                                logger.info("SIGNAL %s (list: %s)", key, list_name)
                            else:
                                logger.error("Telegram send failed for %s (will retry next cycle)", key)

        logger.info(
            "cycle done in %.1fs (%d watchlists)",
            time.time() - cycle_start, len(cfg["lists"]),
        )
        time.sleep(cfg["poll_minutes"] * 60)


if __name__ == "__main__":
    main()
