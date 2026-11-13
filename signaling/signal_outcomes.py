"""Resolve logged signal alerts against later bars: target first, stop first, or expiry.

Uses bars the scheduler already fetched. A symbol that leaves every watchlist
stops updating until it is scanned again. A delivered alert that resolves to
win/loss gets `outcome_notified: false`; signalbot then posts a one-line
follow-up ("target hit" / "stop hit") to the same channels and flips it true.
"""
import pandas as pd

import core.config as config
import signaling.signals as signals

OUTCOME_BARS = 100
RESOLVED = ("win", "loss", "expired")


def bar_key(ts) -> str:
    """Match signalbot's `bar_ts` (YYYY-MM-DD HH:MM)."""
    return str(ts)[:16].replace("T", " ")


def _find_bar(df: pd.DataFrame, bar_ts: str) -> int | None:
    keys = [bar_key(ts) for ts in df.index]
    for i in range(len(keys) - 1, -1, -1):
        if keys[i] == bar_ts:
            return i
    return None


def _hits(direction: str, high: float, low: float, stop: float, target: float) -> tuple[bool, bool]:
    if direction == "long":
        return low <= stop, high >= target
    return high >= stop, low <= target


def resolve_outcome(df: pd.DataFrame, entry: dict, max_bars: int = OUTCOME_BARS) -> str | None:
    """win / loss / expired, or None while the trade is still open or the bar is missing.

    Same-bar stop and target counts as a loss. Entries without a target are skipped.
    """
    if entry.get("outcome") in RESOLVED:
        return entry["outcome"]
    target = entry.get("target")
    stop = entry.get("stop")
    if target is None or stop is None:
        return None
    direction = entry.get("direction")
    if direction not in ("long", "short"):
        return None
    if df is None or df.empty:
        return None
    idx = _find_bar(df, str(entry.get("bar_ts", "")))
    if idx is None:
        return None
    future = df.iloc[idx + 1: idx + 1 + max_bars]
    if future.empty:
        return None
    stop_f = float(stop)
    target_f = float(target)
    for _, row in future.iterrows():
        hit_stop, hit_target = _hits(direction, float(row["High"]), float(row["Low"]), stop_f, target_f)
        if hit_stop:
            return "loss"
        if hit_target:
            return "win"
    if len(future) >= max_bars:
        return "expired"
    return None


def update_pending(bars_cache: dict[tuple[str, str], pd.DataFrame]) -> None:
    """Fill `outcome` on log rows whose symbol/timeframe was fetched this wake."""
    if not bars_cache:
        return

    def apply(entries: list) -> None:
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("outcome") in RESOLVED:
                continue
            pair = (entry.get("symbol"), entry.get("timeframe"))
            df = bars_cache.get(pair)
            if df is None or getattr(df, "empty", True):
                continue
            outcome = resolve_outcome(df, entry)
            if outcome:
                entry["outcome"] = outcome
                if outcome in NOTIFY_OUTCOMES and not entry.get("filtered"):
                    entry["outcome_notified"] = False  # signalbot sends the follow-up

    signals.rewrite_signals(apply)


NOTIFY_OUTCOMES = ("win", "loss")


def pending_notifications() -> list[dict]:
    """Delivered alerts that just hit target/stop and still need a follow-up.
    Rows resolved before this feature have no `outcome_notified` and stay quiet."""
    return [e for e in signals.read_signals(limit=config.SIGNAL_MAX_LOG, include_filtered=False)
            if e.get("outcome_notified") is False]


def mark_notified(keys: set[str]) -> None:
    if not keys:
        return

    def apply(entries: list) -> None:
        for entry in entries:
            if isinstance(entry, dict) and entry.get("key") in keys:
                entry["outcome_notified"] = True

    signals.rewrite_signals(apply)


def outcome_message(entry: dict) -> str:
    """'TSLA · 15m — Bullish Engulfing (LONG): 🎯 target hit $251.20 (+2R)'."""
    side = "LONG" if entry.get("direction") == "long" else "SHORT"
    head = f"{entry.get('symbol')} · {entry.get('timeframe')} — {entry.get('label')} ({side})"
    entry_px = float(entry.get("entry") or entry.get("close") or 0.0)
    stop = float(entry.get("stop") or 0.0)
    target = entry.get("target")
    risk = abs(entry_px - stop)
    if entry.get("outcome") == "win" and target is not None:
        r = abs(float(target) - entry_px) / risk if risk else None
        body = f"🎯 Target hit ${float(target):.2f}" + (f" (+{r:.1f}R)" if r else "")
    else:
        body = f"🛑 Stop hit ${stop:.2f} (-1R)"
    return f"{head}\n{body}\nAlert bar: {entry.get('bar_ts')} · entry ~${entry_px:.2f}"
