"""Shared state for the signal bot and the API: config file, sent-signal log,
heartbeat, and Telegram / Discord send helpers. Both dashboard.py and signalbot.py
import from here so they never disagree about where things live.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import config
from candle_patterns import PATTERNS

VALID_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "1d"]
_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")
_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")

DiscordRoute = Literal["day", "hour", "week"]
DISCORD_ROUTES: tuple[DiscordRoute, ...] = ("day", "hour", "week")
DISCORD_ROUTE_ENV: dict[DiscordRoute, str] = {
    "day": "DISCORD_WEBHOOK_DAY_TRADE",
    "hour": "DISCORD_WEBHOOK_HOUR_TRADE",
    "week": "DISCORD_WEBHOOK_WEEK_TRADE",
}


def _default_list() -> dict:
    return {
        "id": "default",
        "name": "Default",
        "symbols": ["SPY"],
        "timeframes": ["1d"],
        "patterns": list(PATTERNS.keys()),
    }


def default_config() -> dict:
    return {"poll_minutes": 5, "lists": [_default_list()]}


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
        if not _SYMBOL_RE.match(s):
            raise ValueError(f"Invalid symbol '{s}' - letters, digits, dots and dashes only")
        if s not in clean:
            clean.append(s)
    return clean


def _clean_timeframes(tfs) -> list[str]:
    if not isinstance(tfs, list) or not tfs:
        raise ValueError("At least one timeframe is required")
    for tf in tfs:
        if tf not in VALID_TIMEFRAMES:
            raise ValueError(f"Unknown timeframe '{tf}' - use {', '.join(VALID_TIMEFRAMES)}")
    return list(dict.fromkeys(tfs))


def _clean_discord_route(raw) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    route = str(raw).strip().lower()
    if route not in DISCORD_ROUTES:
        raise ValueError(f"discord_route must be one of {', '.join(DISCORD_ROUTES)} or null")
    return route


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
        "poll_minutes": raw.get("poll_minutes", 5),
        "lists": [{
            "id": "default",
            "name": "Default",
            "symbols": raw.get("symbols", ["SPY"]),
            "timeframes": raw.get("timeframes", ["1d"]),
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
            timeframes = _clean_timeframes(item.get("timeframes"))
            patterns = _clean_patterns(item.get("patterns"))
            discord_route = _clean_discord_route(item.get("discord_route"))
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from exc

        out_item = {
            "id": list_id,
            "name": name,
            "symbols": symbols,
            "timeframes": timeframes,
            "patterns": patterns,
        }
        if discord_route is not None:
            out_item["discord_route"] = discord_route
        out_lists.append(out_item)

    try:
        poll = int(cfg.get("poll_minutes", 5))
    except (TypeError, ValueError):
        raise ValueError("Poll interval must be a whole number of minutes")
    if not 1 <= poll <= 60:
        raise ValueError("Poll interval must be 1-60 minutes")

    return {"poll_minutes": poll, "lists": out_lists}


def save_config(raw) -> dict:
    """Validate, persist and return the config."""
    cfg = validate_config(raw)
    _atomic_write(config.SIGNAL_CONFIG_PATH, json.dumps(cfg, indent=2))
    return cfg


# ---- sent-signal log (also the dedupe source of truth) ----

def signal_key(symbol: str, timeframe: str, bar_ts: str, pattern_id: str) -> str:
    return f"{symbol}|{timeframe}|{bar_ts}|{pattern_id}"


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

def write_heartbeat(poll_minutes: int) -> None:
    payload = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "poll_minutes": poll_minutes}
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
