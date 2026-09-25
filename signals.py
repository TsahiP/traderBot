"""Shared state for the signal bot and the API: config file, sent-signal log,
heartbeat, and the Telegram send helpers. Both dashboard.py and signalbot.py
import from here so they never disagree about where things live.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

import config
from candle_patterns import PATTERNS

VALID_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "1d"]
_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")


def default_config() -> dict:
    return {
        "symbols": ["SPY"],
        "timeframes": ["1d"],
        "patterns": list(PATTERNS.keys()),
        "poll_minutes": 5,
    }


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_config() -> dict:
    """Read the config file; fall back to defaults for a missing/corrupt file."""
    path = config.SIGNAL_CONFIG_PATH
    if not path.exists():
        return default_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_config()
    base = default_config()
    for key in base:
        if key in raw and isinstance(raw[key], type(base[key])):
            base[key] = raw[key]
    return base


def validate_config(cfg) -> dict:
    """Coerce + validate a raw config (from the API). Raises ValueError."""
    out = default_config()

    symbols = cfg.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("At least one symbol is required")
    clean = []
    for s in symbols:
        s = str(s).strip().upper()
        if not _SYMBOL_RE.match(s):
            raise ValueError(f"Invalid symbol '{s}' - letters, digits, dots and dashes only")
        if s not in clean:
            clean.append(s)
    out["symbols"] = clean

    tfs = cfg.get("timeframes")
    if not isinstance(tfs, list) or not tfs:
        raise ValueError("At least one timeframe is required")
    for tf in tfs:
        if tf not in VALID_TIMEFRAMES:
            raise ValueError(f"Unknown timeframe '{tf}' - use {', '.join(VALID_TIMEFRAMES)}")
    out["timeframes"] = list(dict.fromkeys(tfs))

    pats = cfg.get("patterns")
    if not isinstance(pats, list) or not pats:
        raise ValueError("At least one pattern is required")
    for pid in pats:
        if pid not in PATTERNS:
            raise ValueError(f"Unknown pattern '{pid}'")
    out["patterns"] = list(dict.fromkeys(pats))

    try:
        poll = int(cfg.get("poll_minutes", 5))
    except (TypeError, ValueError):
        raise ValueError("Poll interval must be a whole number of minutes")
    if not 1 <= poll <= 60:
        raise ValueError("Poll interval must be 1-60 minutes")
    out["poll_minutes"] = poll

    return out


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
