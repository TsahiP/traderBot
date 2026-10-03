"""Signal bot: watches configured symbols/timeframes for Japanese candlestick
patterns and pushes alerts (text + chart image) to Telegram and/or Discord.

Run in its own terminal:  python signalbot.py
Config lives in output/signal_config.json and is re-read each scheduler wake, so edits
made in the Signals tab of the web UI apply without a restart.
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

import config
import signals
from candle_patterns import PATTERNS, detect_all
from chart_image import render_candles
from market_data import fetch_ohlcv, fetch_ohlcv_period
from market_symbols import is_bybit_crypto, normalize_symbol

load_dotenv(config.BASE_DIR / ".env")

NY = ZoneInfo("America/New_York")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("signalbot")
_handler = RotatingFileHandler(
    config.LOG_DIR / "signalbot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)

# How much history to pull per job: enough for 3-bar patterns + a 30-bar chart.
TF_PERIOD = {
    "1m": "5d",
    "5m": "15d",
    "15m": "30d",
    "30m": "60d",
    "1h": "90d",
    "1d": "180d",
    "1w": "5y",
}
_TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
_BYBIT_LOOKBACK = {
    "1m": timedelta(days=5),
    "5m": timedelta(days=15),
    "15m": timedelta(days=30),
    "30m": timedelta(days=60),
    "1h": timedelta(days=90),
    "1d": timedelta(days=180),
    "1w": timedelta(days=365 * 5),
}


def fetch_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    """Recent OHLCV bars with the in-progress bar dropped."""
    symbol = normalize_symbol(symbol)
    if is_bybit_crypto(symbol):
        # Bybit drops the unclosed candle by its UTC end time. A second
        # NY-date check would discard a daily bar that already closed.
        start = datetime.now(timezone.utc) - _BYBIT_LOOKBACK[timeframe]
        return fetch_ohlcv(symbol, timeframe, start=start)

    df = fetch_ohlcv_period(symbol, timeframe, TF_PERIOD[timeframe])

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


def _try_deliver_alert(
    watchlist: dict,
    timeframe: str,
    key: str,
    message: str,
    photo: bytes | None,
    token: str | None,
    chat_ids: list[str],
    symbol: str,
) -> bool:
    """Send to every channel selected for this timeframe; all must succeed."""
    tg_tfs = set(watchlist.get("telegram_timeframes", []))
    dc_tfs = set(watchlist.get("discord_timeframes", []))
    want_tg = timeframe in tg_tfs
    want_dc = timeframe in dc_tfs

    if not want_tg and not want_dc:
        logger.warning(
            "no delivery channel for %s (tf=%s tg=%s dc=%s)",
            key,
            timeframe,
            "on" if want_tg else "off",
            "on" if want_dc else "off",
        )
        return False

    tg_ready = bool(token and chat_ids)
    if want_tg and not tg_ready:
        logger.error("Telegram not configured — cannot deliver %s (will retry)", key)
        return False

    route = (
        signals.discord_route_for_symbol_timeframe(symbol, timeframe) if want_dc else None
    )
    dc_url = signals.discord_webhook_url(route) if route else None
    if want_dc and route and dc_url:
        wh_id = dc_url.split("/webhooks/", 1)[-1].split("/", 1)[0][:12]
        logger.info(
            "Discord route %s %s -> %s (webhook …%s)",
            normalize_symbol(symbol),
            timeframe,
            route,
            wh_id,
        )
    if want_dc and (not route or not dc_url):
        logger.error(
            "Discord webhook missing for route=%s (tf=%s) — cannot deliver %s (will retry)",
            route,
            timeframe,
            key,
        )
        return False

    tg_ok = True
    if want_tg:
        tg_ok = signals.tg_send(token, chat_ids, text=message, photo=photo)

    dc_ok = True
    if want_dc:
        dc_ok = signals.dc_send(dc_url, text=message, photo=photo)

    if want_tg and not tg_ok:
        logger.error("Telegram send failed for %s (will retry next cycle)", key)
    if want_dc and not dc_ok:
        logger.error("Discord send failed for %s (route=%s) (will retry next cycle)", key, route)

    return tg_ok and dc_ok


def _watchlist_by_id(cfg: dict, list_id: str) -> dict | None:
    for watchlist in cfg.get("lists", []):
        if watchlist["id"] == list_id:
            return watchlist
    return None


def _run_scan_job(
    cfg: dict,
    list_id: str,
    list_name: str,
    symbol: str,
    timeframe: str,
    sent: set[str],
    token: str | None,
    chat_ids: list[str],
    bars_cache: dict[tuple[str, str], pd.DataFrame],
) -> None:
    watchlist = _watchlist_by_id(cfg, list_id)
    if watchlist is None:
        return

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

    df = bars_cache[pair]
    if len(df) < 5:
        return

    found = detect_all(df, watchlist["patterns"])
    bar_ts = str(df.index[-1])[:16].replace("T", " ")
    hit = list(found)
    if hit:
        logger.info(
            "scan %s %s [%s] bar=%s patterns=%s",
            symbol,
            timeframe,
            list_name,
            bar_ts,
            ",".join(hit),
        )
    else:
        logger.info("scan %s %s [%s] bar=%s — no pattern", symbol, timeframe, list_name, bar_ts)

    for pattern_id in found:
        key = signals.signal_key(list_id, symbol, timeframe, bar_ts, pattern_id)
        if signals.signal_already_sent(sent, list_id, symbol, timeframe, bar_ts, pattern_id):
            logger.info("dedupe skip %s", key)
            continue

        levels = build_levels(df, pattern_id)
        message = build_message(symbol, timeframe, pattern_id, bar_ts, levels)
        try:
            photo = render_candles(
                df,
                symbol,
                timeframe,
                PATTERNS[pattern_id]["label"],
                PATTERNS[pattern_id]["direction"],
                levels=levels,
            )
        except Exception as exc:
            logger.warning("chart render failed for %s: %s", key, exc)
            photo = None

        if not _try_deliver_alert(
            watchlist, timeframe, key, message, photo, token, chat_ids, symbol
        ):
            logger.warning("delivery failed or blocked for %s — will retry next cycle", key)
            continue

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


def main() -> None:
    logger.info("Starting signal bot (per-watchlist bar scheduler)")
    schedule: dict[tuple[str, str, str], datetime] = {}

    while True:
        wake_start = time.time()
        cfg = signals.load_config()
        signals.reload_discord_webhooks_cache()
        signals.write_heartbeat()
        token, _ = signals.telegram_credentials()
        chat_ids = signals.telegram_chat_ids()
        sent = signals.sent_keys()
        now = datetime.now(NY)

        jobs = signals.expand_scan_jobs(cfg)
        active_keys = {(list_id, symbol, tf) for list_id, _, symbol, tf in jobs}
        for stale in [k for k in schedule if k not in active_keys]:
            del schedule[stale]

        due: list[tuple[str, str, str, str]] = []
        for list_id, list_name, symbol, timeframe in jobs:
            job_key = (list_id, symbol, timeframe)
            if job_key not in schedule:
                schedule[job_key] = now
            if now >= schedule[job_key]:
                due.append((list_id, list_name, symbol, timeframe))

        bars_cache: dict[tuple[str, str], pd.DataFrame] = {}
        for list_id, list_name, symbol, timeframe in due:
            _run_scan_job(
                cfg, list_id, list_name, symbol, timeframe, sent, token, chat_ids, bars_cache
            )
            schedule[(list_id, symbol, timeframe)] = signals.next_run_after_timeframe(timeframe, now)

        elapsed = time.time() - wake_start
        tg_ok = bool(token and chat_ids)
        now = datetime.now(NY)
        if schedule:
            next_wake = min(schedule.values())
            sleep_s = min(
                max(0.0, (next_wake - now).total_seconds()),
                float(signals.SIGNAL_WAKE_CAP_SECONDS),
            )
        else:
            next_wake = now
            sleep_s = float(signals.SIGNAL_WAKE_CAP_SECONDS)

        if due:
            logger.info(
                "wake: scanned %d/%d jobs in %.1fs | tg=%s sent_keys=%d | sleep %.1fs until %s",
                len(due),
                len(schedule),
                elapsed,
                "ok" if tg_ok else "off",
                len(sent),
                sleep_s,
                next_wake.strftime("%H:%M:%S"),
            )
        else:
            logger.info(
                "wake: idle 0/%d jobs due | tg=%s sent_keys=%d | sleep %.1fs until %s",
                len(schedule),
                "ok" if tg_ok else "off",
                len(sent),
                sleep_s,
                next_wake.strftime("%H:%M:%S"),
            )

        time.sleep(max(sleep_s, 0.1))


if __name__ == "__main__":
    main()

