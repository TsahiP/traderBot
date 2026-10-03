"""Shared state for the signal bot and the API: config file, sent-signal log,
heartbeat, and Telegram / Discord send helpers. Both dashboard.py and signalbot.py
import from here so they never disagree about where things live.
"""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import config
from candle_patterns import PATTERNS

VALID_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "1d"]
VALID_DISCORD_TIMEFRAMES = [*VALID_TIMEFRAMES, "1w"]


def _coerce_discord_timeframe_list(tfs) -> list:
    """Normalize legacy `w` to canonical `1w` before validation."""
    if not isinstance(tfs, list):
        return tfs
    return ["1w" if tf == "w" else tf for tf in tfs]
_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")
# yfinance: bare BTC/ETH are Grayscale ETFs, not spot crypto.
_YFINANCE_SYMBOL_ALIASES = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")

DiscordRoute = Literal["day", "hour", "minute", "week"]
DISCORD_ROUTES: tuple[DiscordRoute, ...] = ("day", "hour", "minute", "week")
DISCORD_ROUTE_ENV: dict[DiscordRoute, str] = {
    "day": "DISCORD_WEBHOOK_DAY_TRADE",
    "hour": "DISCORD_WEBHOOK_HOUR_TRADE",
    "minute": "DISCORD_WEBHOOK_MINUTE_TRADE",
    "week": "DISCORD_WEBHOOK_WEEK_TRADE",
}

TIMEFRAME_DISCORD_ROUTE: dict[str, DiscordRoute] = {
    "1m": "minute",
    "5m": "minute",
    "15m": "minute",
    "30m": "minute",
    "1h": "hour",
    "1d": "day",
    "1w": "week",
}
DISCORD_TIMEFRAME_ORDER: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "1d", "1w")

NY = ZoneInfo("America/New_York")
BAR_CLOSE_OFFSET_SECONDS = 45
SIGNAL_WAKE_CAP_SECONDS = 15
SIGNAL_HEARTBEAT_STALE_SECONDS = 120
_SCHEDULER_TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def _default_list() -> dict:
    return {
        "id": "default",
        "name": "Default",
        "symbols": ["SPY"],
        "telegram_timeframes": ["1d"],
        "discord_timeframes": ["1d"],
        "patterns": list(PATTERNS.keys()),
    }


def default_config() -> dict:
    return {"lists": [_default_list()]}


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _clean_symbols(symbols) -> list[str]:
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("At least one symbol is required")
    clean = []
    for s in symbols:
        s = str(s).strip().upper()
        s = _YFINANCE_SYMBOL_ALIASES.get(s, s)
        if not _SYMBOL_RE.match(s):
            raise ValueError(f"Invalid symbol '{s}' - letters, digits, dots and dashes only")
        if s not in clean:
            clean.append(s)
    return clean


def _clean_timeframes(
    tfs,
    label: str = "timeframe",
    *,
    allowed: list[str] | None = None,
) -> list[str]:
    allowed = allowed or VALID_TIMEFRAMES
    if not isinstance(tfs, list) or not tfs:
        raise ValueError(f"At least one {label} is required")
    for tf in tfs:
        if tf not in allowed:
            raise ValueError(f"Unknown timeframe '{tf}' - use {', '.join(allowed)}")
    return list(dict.fromkeys(tfs))


def _resolve_channel_timeframes(item: dict) -> tuple[list[str], list[str]]:
    """telegram_timeframes / discord_timeframes, with legacy `timeframes` for both."""
    legacy = item.get("timeframes")
    tg_raw = item.get("telegram_timeframes")
    dc_raw = item.get("discord_timeframes")
    if tg_raw is None:
        tg_raw = legacy if legacy is not None else ["1d"]
    if dc_raw is None:
        dc_raw = legacy if legacy is not None else ["1d"]
    return tg_raw, dc_raw


def watchlist_scan_timeframes(watchlist: dict) -> list[str]:
    """Union of channel timeframes (deduped, stable order)."""
    tfs: list[str] = []
    for tf in watchlist.get("telegram_timeframes", []) + watchlist.get("discord_timeframes", []):
        if tf not in tfs:
            tfs.append(tf)
    return tfs


def discord_route_for_timeframe(timeframe: str) -> DiscordRoute | None:
    return TIMEFRAME_DISCORD_ROUTE.get(timeframe)


def discord_timeframes_available() -> list[str]:
    out: list[str] = []
    for tf in DISCORD_TIMEFRAME_ORDER:
        route = discord_route_for_timeframe(tf)
        if route and discord_webhook_url(route):
            out.append(tf)
    return out


def expand_scan_jobs(cfg: dict) -> list[tuple[str, str, str, str]]:
    """(list_id, list_name, symbol, timeframe) for each watchlist's own symbols + TFs."""
    jobs: list[tuple[str, str, str, str]] = []
    for watchlist in cfg.get("lists", []):
        list_id = watchlist["id"]
        list_name = watchlist["name"]
        for symbol in watchlist["symbols"]:
            for timeframe in watchlist_scan_timeframes(watchlist):
                jobs.append((list_id, list_name, symbol, timeframe))
    return jobs


def next_run_after_timeframe(timeframe: str, after: datetime | None = None) -> datetime:
    """Next check time: start of the next bar period in NY + close offset."""
    now = after or datetime.now(NY)
    if now.tzinfo is None:
        now = now.replace(tzinfo=NY)
    else:
        now = now.astimezone(NY)
    offset = timedelta(seconds=BAR_CLOSE_OFFSET_SECONDS)

    if timeframe == "1w":
        days_ahead = (7 - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_monday = (now + timedelta(days=days_ahead)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return next_monday + offset

    if timeframe == "1d":
        next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return next_day + offset

    minutes = _SCHEDULER_TF_MINUTES.get(timeframe)
    if minutes is None:
        return now + timedelta(minutes=5)

    if minutes >= 60:
        boundary = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        bucket_min = (now.minute // minutes) * minutes
        boundary = now.replace(minute=bucket_min, second=0, microsecond=0) + timedelta(minutes=minutes)
    return boundary + offset


def _clean_discord_timeframes(tfs) -> list[str]:
    if not isinstance(tfs, list):
        raise ValueError("Discord timeframes must be a list")
    if not tfs:
        return []
    return _clean_timeframes(
        _coerce_discord_timeframe_list(tfs),
        "Discord timeframe",
        allowed=VALID_DISCORD_TIMEFRAMES,
    )


def _clean_patterns(pats) -> list[str]:
    if not isinstance(pats, list) or not pats:
        raise ValueError("At least one pattern is required")
    for pid in pats:
        if pid not in PATTERNS:
            raise ValueError(f"Unknown pattern '{pid}'")
    return list(dict.fromkeys(pats))


def _slug_id(name: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "list"
    base = base[:32].strip("-") or "list"
    candidate = base
    n = 2
    while candidate in used or not _ID_RE.match(candidate):
        suffix = f"-{n}"
        candidate = f"{base[:32 - len(suffix)]}{suffix}"
        n += 1
    return candidate


def _migrate_flat(raw: dict) -> dict:
    """Old single-watchlist file -> one list named Default."""
    return {
        "lists": [{
            "id": "default",
            "name": "Default",
            "symbols": raw.get("symbols", ["SPY"]),
            "telegram_timeframes": raw.get("timeframes", ["1d"]),
            "discord_timeframes": raw.get("timeframes", ["1d"]),
            "patterns": raw.get("patterns", list(PATTERNS.keys())),
        }],
    }


def load_config() -> dict:
    """Read the config file; fall back to defaults for a missing/corrupt file.

    A flat file (symbols/timeframes/patterns, no lists) loads as one Default list.
    The file itself is rewritten only on the next save.
    """
    path = config.SIGNAL_CONFIG_PATH
    if not path.exists():
        return default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_config()
    if not isinstance(raw, dict):
        return default_config()
    if "lists" not in raw and "symbols" in raw:
        raw = _migrate_flat(raw)
    try:
        return validate_config(raw)
    except ValueError:
        return default_config()


def validate_config(cfg) -> dict:
    """Coerce + validate a raw config (from the API). Raises ValueError."""
    if not isinstance(cfg, dict):
        raise ValueError("Config must be an object")

    lists = cfg.get("lists")
    if not isinstance(lists, list) or not lists:
        raise ValueError("At least one watchlist is required")

    out_lists = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for i, item in enumerate(lists):
        if not isinstance(item, dict):
            raise ValueError(f"Watchlist {i + 1} must be an object")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError(f"Watchlist {i + 1} needs a name")
        name_key = name.casefold()
        if name_key in seen_names:
            raise ValueError(f"Duplicate watchlist name '{name}'")
        seen_names.add(name_key)

        raw_id = str(item.get("id") or "").strip().lower()
        if raw_id:
            if not _ID_RE.match(raw_id):
                raise ValueError(f"Invalid watchlist id '{raw_id}'")
            if raw_id in seen_ids:
                raise ValueError(f"Duplicate watchlist id '{raw_id}'")
            list_id = raw_id
        else:
            list_id = _slug_id(name, seen_ids)
        seen_ids.add(list_id)

        label = f"Watchlist '{name}'"
        try:
            symbols = _clean_symbols(item.get("symbols"))
            tg_raw, dc_raw = _resolve_channel_timeframes(item)
            telegram_timeframes = _clean_timeframes(tg_raw, "Telegram timeframe")
            discord_timeframes = _clean_discord_timeframes(dc_raw)
            patterns = _clean_patterns(item.get("patterns"))
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from exc

        out_lists.append({
            "id": list_id,
            "name": name,
            "symbols": symbols,
            "telegram_timeframes": telegram_timeframes,
            "discord_timeframes": discord_timeframes,
            "patterns": patterns,
        })

    return {"lists": out_lists}


def save_config(raw) -> dict:
    """Validate, persist and return the config."""
    cfg = validate_config(raw)
    _atomic_write(config.SIGNAL_CONFIG_PATH, json.dumps(cfg, indent=2))
    return cfg


# ---- sent-signal log (also the dedupe source of truth) ----

def signal_key(
    list_id: str,
    symbol: str,
    timeframe: str,
    bar_ts: str,
    pattern_id: str,
) -> str:
    return f"{list_id}|{symbol}|{timeframe}|{bar_ts}|{pattern_id}"


def legacy_signal_key(
    symbol: str,
    timeframe: str,
    bar_ts: str,
    pattern_id: str,
) -> str:
    """Pre–multi-watchlist dedupe key (no list_id prefix)."""
    return f"{symbol}|{timeframe}|{bar_ts}|{pattern_id}"


def signal_already_sent(
    sent: set[str],
    list_id: str,
    symbol: str,
    timeframe: str,
    bar_ts: str,
    pattern_id: str,
) -> bool:
    if signal_key(list_id, symbol, timeframe, bar_ts, pattern_id) in sent:
        return True
    return legacy_signal_key(symbol, timeframe, bar_ts, pattern_id) in sent


def read_signals(limit: int = 50) -> list[dict]:
    path = config.SIGNAL_LOG_PATH
    if not path.exists():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return list(reversed(entries[-limit:]))


def sent_keys() -> set[str]:
    path = config.SIGNAL_LOG_PATH
    if not path.exists():
        return set()
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {e["key"] for e in entries if "key" in e}


def append_signal(entry: dict) -> None:
    path = config.SIGNAL_LOG_PATH
    entries = []
    if path.exists():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []
    entries.append(entry)
    _atomic_write(path, json.dumps(entries[-config.SIGNAL_MAX_LOG:], indent=2))


# ---- heartbeat (how the API knows the bot is alive) ----

def write_heartbeat() -> None:
    payload = {"ts": datetime.now().astimezone().isoformat(timespec="seconds")}
    _atomic_write(config.SIGNAL_HEARTBEAT_PATH, json.dumps(payload))


def read_heartbeat() -> dict | None:
    path = config.SIGNAL_HEARTBEAT_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ---- Telegram ----

def telegram_credentials() -> tuple[str | None, str | None]:
    return os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")


def telegram_chat_ids() -> list[str]:
    """All target chat ids from TELEGRAM_CHAT_ID (comma/space separated)."""
    raw = os.getenv("TELEGRAM_CHAT_ID", "") or ""
    return [c.strip() for c in re.split(r"[,\s]+", raw) if c.strip()]


def tg_send(token: str, chat_ids: list[str], text: str | None = None,
            photo: bytes | None = None) -> bool:
    """Send a text message and/or a PNG to every chat id. True only if all succeed."""
    import requests

    base = f"https://api.telegram.org/bot{token}"
    ok_all = True
    for cid in chat_ids:
        try:
            if photo is not None:
                r = requests.post(
                    f"{base}/sendPhoto",
                    data={"chat_id": cid, "caption": text or ""},
                    files={"photo": ("signal.png", photo, "image/png")},
                    timeout=30,
                )
            else:
                r = requests.post(f"{base}/sendMessage", json={"chat_id": cid, "text": text}, timeout=30)
            if not r.json().get("ok"):
                ok_all = False
        except Exception:
            ok_all = False
    return ok_all


# ---- Discord (incoming webhooks) ----

def discord_webhook_url(route: str | None) -> str | None:
    if route not in DISCORD_ROUTES:
        return None
    url = (os.getenv(DISCORD_ROUTE_ENV[route]) or "").strip()
    return url or None


def discord_configured() -> dict[str, bool]:
    return {r: bool(discord_webhook_url(r)) for r in DISCORD_ROUTES}


def dc_send(webhook_url: str, text: str | None = None, photo: bytes | None = None) -> bool:
    """Post to a Discord incoming webhook. True on HTTP 2xx."""
    import requests

    try:
        if photo is not None:
            payload = {"content": text or ""}
            r = requests.post(
                webhook_url,
                data={"payload_json": json.dumps(payload)},
                files={"file": ("signal.png", photo, "image/png")},
                timeout=30,
            )
        else:
            r = requests.post(webhook_url, json={"content": text or ""}, timeout=30)
        return r.ok
    except Exception:
        return False
