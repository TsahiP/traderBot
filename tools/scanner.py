"""Market scanner: find setups across US-listed stocks before the open (or ORBs during it).

Idea from the day-trader-skill pack's trader-scanner, rebuilt on yfinance:

- Universe: Nasdaq Trader's symbol directories (nasdaqlisted + otherlisted),
  common stock / ordinary shares / ADRs only (no ETFs, test issues, warrants,
  units, rights, notes, preferreds). Cached for a day in output/.
- Daily scan (`daily`): ~3 months of daily bars in chunks via yf.download, then
  a liquidity filter (last close >= min_price, last volume >= min_volume) and:
    breakout_20d     close above every close of the 20 sessions before it
    bull_flag        +10% over the 10 sessions before the last 5, last 5 closes within 5%
    reclaim_ma20     previous close under its 20-day average, last close above it
    oversold         RSI 14 (Wilder) under 30
    oversold_bounce  RSI 14 under 30 yesterday and an up close today
- Opening-range scan (`orb`): today's 5m bars; the first completed bar after
  the first `or_minutes` that CLOSES above the range high.

Hits are sorted most traded first (price x volume) and written to
output/scan_results.json. Run: python scanner.py [daily|orb] [--scope watchlists|universe]
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import time as _time
from datetime import datetime, time, timedelta

import pandas as pd
import requests

import core.config as config
import market.market_calendar as market_calendar
from charts.confirmations import rsi

logger = logging.getLogger("scanner")

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
UNIVERSE_PATH = config.OUTPUT_DIR / "scanner_universe.json"
RESULTS_PATH = config.OUTPUT_DIR / "scan_results.json"
UNIVERSE_TTL_H = 20
CHUNK = 200
CHUNK_PAUSE_S = 1.0
MIN_PRICE = 5.0
MIN_VOLUME = 500_000
OR_MINUTES = 15

SETUPS = ("breakout_20d", "bull_flag", "reclaim_ma20", "oversold", "oversold_bounce")
_EXCLUDE_NAME = re.compile(
    r"\b(warrants?|units?|rights?|notes?|debentures?|preferred|preference|etns?|"
    r"depositary shares? representing|%)", re.I)
_TICKER = re.compile(r"^[A-Z]{1,5}(-[A-Z])?$")


# ---------------------------------------------------------------- universe

def _parse_dir(text: str, symbol_col: str) -> list[dict]:
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("File Creation Time")]
    df = pd.read_csv(io.StringIO("\n".join(lines)), sep="|", dtype=str)
    out = []
    for _, r in df.iterrows():
        sym = str(r.get(symbol_col) or "").strip().replace(".", "-")
        name = str(r.get("Security Name") or "")
        if (r.get("Test Issue") == "Y" or r.get("ETF") == "Y" or not _TICKER.match(sym)
                or _EXCLUDE_NAME.search(name)):
            continue
        if r.get("Financial Status") not in (None, "N") and symbol_col == "Symbol":
            continue  # nasdaqlisted: deficient / delinquent / bankrupt
        out.append({"symbol": sym, "name": name[:80]})
    return out


def parse_universe(nasdaq_text: str, other_text: str) -> list[dict]:
    rows = _parse_dir(nasdaq_text, "Symbol") + _parse_dir(other_text, "ACT Symbol")
    seen, out = set(), []
    for r in rows:
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            out.append(r)
    return out


def load_universe(force: bool = False) -> list[dict]:
    try:
        cached = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
        age_h = (datetime.now().timestamp() - cached["ts"]) / 3600
        if not force and age_h < UNIVERSE_TTL_H and cached.get("symbols"):
            return cached["symbols"]
    except (OSError, ValueError, KeyError):
        pass
    texts = []
    for url in (NASDAQ_URL, OTHER_URL):
        resp = requests.get(url, timeout=30, headers={"User-Agent": "tradebot-scanner"})
        resp.raise_for_status()
        texts.append(resp.text)
    symbols = parse_universe(*texts)
    UNIVERSE_PATH.write_text(json.dumps({"ts": datetime.now().timestamp(), "symbols": symbols}),
                             encoding="utf-8")
    return symbols


def watchlist_symbols() -> list[str]:
    import signaling.signals as signals
    from market.market_symbols import is_bybit_crypto

    syms: list[str] = []
    for wl in signals.load_config().get("lists", []):
        for s in wl.get("symbols", []):
            if not is_bybit_crypto(s) and s not in syms:
                syms.append(s)
    return syms


# ---------------------------------------------------------------- setups

def daily_setups(df: pd.DataFrame) -> list[str]:
    """Setup ids that fire on the last completed daily bar."""
    c = df["Close"].astype(float).dropna()
    hits: list[str] = []
    if len(c) >= 21 and c.iloc[-1] > c.iloc[-21:-1].max():
        hits.append("breakout_20d")
    if len(c) >= 16:
        pole = c.iloc[-6] / c.iloc[-16] - 1
        flag = c.iloc[-5:]
        if pole > 0.10 and (flag.max() - flag.min()) / flag.max() <= 0.05:
            hits.append("bull_flag")
    if len(c) >= 21:
        ma = c.rolling(20).mean()
        if c.iloc[-2] < ma.iloc[-2] and c.iloc[-1] > ma.iloc[-1]:
            hits.append("reclaim_ma20")
    if len(c) >= 16:
        r = rsi(c)
        if r.iloc[-1] < 30:
            hits.append("oversold")
        if r.iloc[-2] < 30 and c.iloc[-1] > c.iloc[-2]:
            hits.append("oversold_bounce")
    return hits


def _completed_session(df5: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Today's 5-minute bars that have already closed. Yesterday's session is dropped."""
    idx = pd.DatetimeIndex(df5.index)
    df5 = df5.copy()
    df5.index = idx.tz_localize(market_calendar.NY) if idx.tz is None else idx.tz_convert(market_calendar.NY)
    day = now.date()
    session = df5[(df5.index.date == day) & (df5.index.time >= time(9, 30))]
    return session[session.index + timedelta(minutes=5) <= now]


def orb_state(
    df5: pd.DataFrame | None,
    or_minutes: int = OR_MINUTES,
    now: datetime | None = None,
    min_volume_x: float = 0.0,
    no_entries_after: str = "15:30",
) -> dict:
    """Long-only opening-range state for one chart.

    A trigger is the first completed bar after the range that CLOSES above the
    range high (a wick does not count). `min_volume_x` also requires that bar's
    volume versus the range bars' average. Status is forming, waiting,
    triggered, expired, or no_data.
    """
    now = (now or datetime.now(market_calendar.NY)).astimezone(market_calendar.NY)
    empty = {"range_high": None, "range_low": None, "last_close": None, "entry": None, "trigger_at": None}
    if df5 is None or df5.empty:
        return {"status": "no_data", "sentence": "no bars", **empty}
    session = _completed_session(df5, now)
    if session.empty:
        return {"status": "no_data", "sentence": "no completed bars from today's session yet", **empty}
    day = now.date()
    or_end = datetime.combine(day, time(9, 30), market_calendar.NY) + timedelta(minutes=or_minutes)
    rng = session[session.index < or_end]
    need = max(or_minutes // 5, 1)
    last_close = round(float(session["Close"].iloc[-1]), 2)
    if len(rng) < need:
        return {"status": "forming", "sentence": f"opening range still forming ({len(rng)} of {need} bars)",
                **empty, "last_close": last_close}
    hi, lo = float(rng["High"].max()), float(rng["Low"].min())
    avg_vol = float(rng["Volume"].mean() or 0)
    for ts, bar in session[session.index >= or_end].iterrows():
        close = float(bar["Close"])
        if close <= hi:
            continue
        if min_volume_x and avg_vol and float(bar["Volume"]) < min_volume_x * avg_vol:
            continue
        at = (ts + timedelta(minutes=5)).strftime("%H:%M")
        over = (close - hi) / hi * 100
        return {
            "status": "triggered",
            "sentence": f"triggered {at} at ${close:.2f} ({over:.2f}% over the range high ${hi:.2f})",
            "range_high": round(hi, 2), "range_low": round(lo, 2), "last_close": last_close,
            "entry": round(close, 2), "trigger_at": at,
        }
    hh, mm = (int(x) for x in no_entries_after.split(":"))
    cutoff = datetime.combine(day, time(hh, mm), market_calendar.NY)
    if day in market_calendar.early_closes(day.year):
        cutoff -= timedelta(hours=3)
    under = (hi - last_close) / hi * 100 if hi else 0
    if now >= cutoff:
        return {"status": "expired",
                "sentence": f"no close above ${hi:.2f} before {cutoff:%H:%M}",
                "range_high": round(hi, 2), "range_low": round(lo, 2), "last_close": last_close,
                "entry": None, "trigger_at": None}
    return {"status": "waiting",
            "sentence": f"last close ${last_close:.2f} is {under:.2f}% under the range high ${hi:.2f}",
            "range_high": round(hi, 2), "range_low": round(lo, 2), "last_close": last_close,
            "entry": None, "trigger_at": None}


def orb_breakout(df5: pd.DataFrame, or_minutes: int = OR_MINUTES, now: datetime | None = None) -> dict | None:
    """First completed 5m bar after the opening range that closes above its high."""
    state = orb_state(df5, or_minutes=or_minutes, now=now)
    if state["status"] != "triggered":
        return None
    return {"at": state["trigger_at"], "close": state["entry"],
            "range_high": state["range_high"], "range_low": state["range_low"]}


def orb_not_ready(now: datetime, or_minutes: int = OR_MINUTES) -> str | None:
    """Why an opening-range scan cannot run now (None when it can)."""
    sess = market_calendar.session(now.date())
    if sess is None:
        return f"NYSE is closed on {now.date():%a %b %d} - the opening-range scan needs today's session"
    ready = sess[0] + timedelta(minutes=or_minutes + 5)  # range + one completed bar after it
    if now < ready:
        return f"opening-range scan runs from {ready:%H:%M} NY (range = first {or_minutes} min)"
    return None


# ---------------------------------------------------------------- data

def _download(symbols: list[str], **kwargs) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        try:
            raw = yf.download(chunk, group_by="ticker", auto_adjust=True, progress=False,
                              threads=True, **kwargs)
        except Exception as exc:  # noqa: BLE001 - one bad chunk must not kill the scan
            logger.warning("chunk %d failed: %s", i // CHUNK, exc)
            continue
        for sym in chunk:
            try:
                df = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            df = df.dropna(how="all")
            if len(df):
                out[sym] = df
        if i + CHUNK < len(symbols):
            _time.sleep(CHUNK_PAUSE_S)
    return out


def _liquid(df: pd.DataFrame, min_price: float, min_volume: float) -> bool:
    last = df.iloc[-1]
    return float(last["Close"]) >= min_price and float(last["Volume"] or 0) >= min_volume


# ---------------------------------------------------------------- scans

def run_scan(
    mode: str = "daily",
    *,
    symbols: list[str] | None = None,
    scope: str = "watchlists",
    min_price: float = MIN_PRICE,
    min_volume: float = MIN_VOLUME,
    now: datetime | None = None,
    fetch=_download,
) -> dict:
    """Scan and write output/scan_results.json. `fetch` is swappable for tests."""
    now = (now or datetime.now(market_calendar.NY)).astimezone(market_calendar.NY)
    names: dict[str, str] = {}
    scope_label = scope if symbols is None else "symbols"
    if symbols is None:
        if scope == "universe":
            uni = load_universe()
            symbols = [u["symbol"] for u in uni]
            names = {u["symbol"]: u["name"] for u in uni}
        else:
            symbols = watchlist_symbols()
    started = _time.time()
    hits: list[dict] = []
    note = None
    data: dict = {}
    if mode == "orb" and (orb_closed := orb_not_ready(now)):
        note = orb_closed
    elif mode == "orb":
        data = fetch(symbols, period="1d", interval="5m")
        for sym, df in data.items():
            hit = orb_breakout(df, now=now)
            if hit:
                vol = float(df["Volume"].sum())
                hits.append({"symbol": sym, "name": names.get(sym), "setups": ["orb"],
                             "close": hit["close"], "volume": vol,
                             "dollar_volume": round(vol * hit["close"]), **hit})
    else:
        data = fetch(symbols, period="3mo", interval="1d")
        for sym, df in data.items():
            if df.index[-1].date() == now.date() and market_calendar.is_open(now):
                df = df.iloc[:-1]                      # today's bar is not complete yet
            if len(df) < 21 or not _liquid(df, min_price, min_volume):
                continue
            setups = daily_setups(df)
            if setups:
                c = df["Close"].astype(float)
                vol = float(df["Volume"].iloc[-1])
                hits.append({
                    "symbol": sym, "name": names.get(sym), "setups": setups,
                    "close": round(float(c.iloc[-1]), 2),
                    "change_pct": round(float(c.iloc[-1] / c.iloc[-2] * 100 - 100), 2),
                    "rsi": round(float(rsi(c).iloc[-1]), 1),
                    "volume": vol, "dollar_volume": round(vol * float(c.iloc[-1])),
                })
    hits.sort(key=lambda h: h["dollar_volume"], reverse=True)
    result = {
        "ts": now.isoformat(timespec="seconds"),
        "mode": mode,
        "scope": scope_label,
        "requested": len(symbols),
        "with_data": len(data),
        "seconds": round(_time.time() - started, 1),
        "filters": {"min_price": min_price, "min_volume": min_volume},
        "hits": hits,
    }
    if note:
        result["note"] = note
    RESULTS_PATH.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def read_results() -> dict | None:
    try:
        return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", nargs="?", default="daily", choices=("daily", "orb"))
    ap.add_argument("--scope", default="watchlists", choices=("watchlists", "universe"))
    ap.add_argument("--symbols", help="comma-separated tickers instead of a scope")
    ap.add_argument("--min-price", type=float, default=MIN_PRICE)
    ap.add_argument("--min-volume", type=float, default=MIN_VOLUME)
    a = ap.parse_args()
    syms = [s.strip().upper() for s in a.symbols.split(",")] if a.symbols else None
    res = run_scan(a.mode, symbols=syms, scope=a.scope, min_price=a.min_price, min_volume=a.min_volume)
    if res.get("note"):
        print(res["note"])
    print(f"{res['mode']} scan: {len(res['hits'])} hits from {res['with_data']}/{res['requested']} "
          f"symbols in {res['seconds']}s -> {RESULTS_PATH}")
    for h in res["hits"][:40]:
        print(f"  {h['symbol']:<6} {h['close']:>9.2f}  {', '.join(h['setups'])}")


if __name__ == "__main__":
    main()
