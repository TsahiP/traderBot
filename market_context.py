"""Analyst step 2 - market context: QQQ trend, VIX, oil, ESLT rotation proxy
and geopolitical headlines. All values computed here; the LLM only reads them.

Fetched through yfinance (already the project's stock source) with a shared
TTL cache (MACRO_TTL_MIN) so several analyses in a row don't hammer the feed.
Partial failures mark their field `n/a` - the context building never aborts.
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import news_client
import market_data
from confirmations import ema
from smc import snapshot_smc

NY = ZoneInfo("America/New_York")

CACHE: dict = {"ts": 0.0, "data": None}

MACRO_FETCH_PERIODS = {
    "QQQ": "1y",
    "^VIX": "3mo",
    "CL=F": "3mo",
    "BZ=F": "3mo",
    "ESLT": "3mo",
}

QQQ_TREND_EMAS = (20, 50, 200)


def _ttl_seconds() -> float:
    return max(0.0, float(config.MACRO_TTL_MIN)) * 60.0


def _last_change_pct(df) -> dict:
    """Daily + 5-bar change of the last close. Empty frame -> n/a."""
    if df is None or len(df) < 2:
        return {"daily_pct": None, "five_bar_pct": None}
    close = df["Close"].astype(float)
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    out = {"daily_pct": round((last - prev) / prev * 100.0, 2) if prev else None}
    out["five_bar_pct"] = None
    if len(close) >= 6:
        back5 = float(close.iloc[-6])
        if back5:
            out["five_bar_pct"] = round((last - back5) / back5 * 100.0, 2)
    out["last"] = round(last, 2)
    return out


def _above_ema_flags(close) -> dict:
    out = {}
    for period in QQQ_TREND_EMAS:
        if len(close) < period:
            out[f"above_ema_{period}"] = None
        else:
            out[f"above_ema_{period}"] = bool(
                float(close.iloc[-1]) > float(ema(close, period).iloc[-1])
            )
    return out


def _qqq_context() -> dict:
    df = market_data.fetch_yfinance_history(
        "QQQ", period=MACRO_FETCH_PERIODS["QQQ"], interval="1d", auto_adjust=True
    )
    if df is None or df.empty:
        return {"ok": False, "error": "no bars"}
    ctx = _last_change_pct(df)
    close = df["Close"].astype(float)
    ctx["trend"] = _above_ema_flags(close)
    try:
        smc = snapshot_smc(df, radius=5)
        ctx["price_zone"] = smc.get("price_zone", "n/a")
        ctx["price_position_pct"] = smc.get("price_position_pct")
    except Exception:  # noqa: BLE001 - SMC context is optional
        ctx["price_zone"] = "n/a"
        ctx["price_position_pct"] = None
    ctx["ok"] = True
    return ctx


def _level_context(symbol: str) -> dict:
    df = market_data.fetch_yfinance_history(
        symbol, period=MACRO_FETCH_PERIODS.get(symbol, "3mo"), interval="1d",
        auto_adjust=True,
    )
    if df is None or df.empty:
        return {"ok": False, "error": "no bars"}
    return {"ok": True, **_last_change_pct(df)}


def _geopolitics_context() -> dict:
    headlines = news_client.geopolitical_headlines()
    return {"ok": True, "headlines": headlines[:6], "count": len(headlines)}


def market_context(*, refresh: bool = False) -> dict:
    """QQQ / VIX / oil / ESLT / geopolitics block, cached for MACRO_TTL_MIN."""
    now = time.monotonic()
    if not refresh and CACHE["data"] is not None:
        if now - CACHE["ts"] < _ttl_seconds():
            return dict(CACHE["data"])

    ctx: dict = {
        "qqq": None, "vix": None, "oil": None, "eslt": None, "geopolitics": None,
    }

    try:
        ctx["qqq"] = _qqq_context()
    except Exception as exc:  # noqa: BLE001 - partial failure must not abort
        ctx["qqq"] = {"ok": False, "error": str(exc)[:80]}
    try:
        ctx["vix"] = _level_context("^VIX")
    except Exception as exc:  # noqa: BLE001
        ctx["vix"] = {"ok": False, "error": str(exc)[:80]}

    oil = {}
    for name, symbol in (("wti", "CL=F"), ("brent", "BZ=F")):
        try:
            oil[name] = _level_context(symbol)
        except Exception as exc:  # noqa: BLE001
            oil[name] = {"ok": False, "error": str(exc)[:80]}
    ctx["oil"] = oil
    try:
        ctx["eslt"] = _level_context("ESLT")
    except Exception as exc:  # noqa: BLE001
        ctx["eslt"] = {"ok": False, "error": str(exc)[:80]}
    try:
        ctx["geopolitics"] = _geopolitics_context()
    except Exception as exc:  # noqa: BLE001
        ctx["geopolitics"] = {"ok": False, "error": str(exc)[:80]}

    result = {**ctx, "updated": datetime.now(NY).isoformat(timespec="seconds")}
    CACHE["ts"] = now
    CACHE["data"] = result
    return dict(result)


def clear_cache() -> None:
    CACHE["data"] = None
    CACHE["ts"] = 0.0
