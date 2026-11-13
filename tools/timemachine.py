"""Time Machine: replay the rest of the session thousands of times before you risk a dollar.

Idea from the day-trader-skill pack, rebuilt for this repo. From the stock's
own 5-minute bars (yfinance keeps ~60 days):

1. The pool: only FULL regular sessions (78 bars, 9:30-15:55 NY), today left
   out. Every bar becomes four ratios to the close before it (O/H/L/C over the
   previous close), so a pool bar can be scaled onto any price.
2. One replay: start at the entry price at the entry slot; for each slot left
   in the day, draw a past bar from the same time of day (+/- `jitter` slots)
   at random and scale it. Stop is checked before target, so a bar touching
   both counts as the stop.
3. N replays give the odds: target first, stop first, neither by `until`,
   and green (target first, or above the entry at `until`). R is +R at the
   target, -1 at the stop, else the distance at `until` over the risk.
4. History: the same levels (as % of the entry) run once on every past
   session from the same slot - what really happened, not a simulation.
5. The verdict compares target-first share of the decided replays with the
   break-even rate 1 / (1 + R).

Seeded (`random_state`, default 19): same plan + same data + same seed = same numbers.
"""
from __future__ import annotations

from datetime import datetime, time

import numpy as np
import pandas as pd

from market.market_calendar import NY, early_closes, is_trading_day

SLOTS = 78                 # 5-min bars in a full 9:30-16:00 session
MIN_SESSIONS = 20
DEFAULT_RUNS = 10_000
DEFAULT_SEED = 19


class TimeMachineError(ValueError):
    pass


def slot_of(at: datetime | time) -> int:
    """5-min slot index of a NY wall-clock time (9:30 -> 0, 15:55 -> 77)."""
    t = at.timetz() if isinstance(at, datetime) else at
    return (t.hour * 60 + t.minute - (9 * 60 + 30)) // 5


def session_pool(df5: pd.DataFrame, before: datetime | None = None) -> tuple[np.ndarray, list]:
    """(ratios[S, 78, 4], session dates) from full regular sessions only."""
    if df5 is None or df5.empty:
        raise TimeMachineError("no 5-minute bars")
    idx = pd.DatetimeIndex(df5.index)
    idx = idx.tz_localize(NY) if idx.tz is None else idx.tz_convert(NY)
    df = df5.copy()
    df.index = idx
    cutoff = (before or datetime.now(NY)).astimezone(NY).date()

    pools, dates = [], []
    prev_close = None
    for day, bars in df.groupby(df.index.date):
        bars = bars[(bars.index.time >= time(9, 30)) & (bars.index.time < time(16, 0))]
        last_close = float(bars["Close"].iloc[-1]) if len(bars) else prev_close
        full = (
            len(bars) == SLOTS and day < cutoff and is_trading_day(day)
            and day not in early_closes(day.year)
            and [slot_of(t) for t in bars.index.time] == list(range(SLOTS))
        )
        if full:
            c = bars["Close"].to_numpy(float)
            prev = np.r_[prev_close if prev_close else float(bars["Open"].iloc[0]), c[:-1]]
            ratios = np.stack([bars[k].to_numpy(float) / prev for k in ("Open", "High", "Low", "Close")], axis=1)
            if np.all(np.isfinite(ratios)) and np.all(ratios > 0):
                pools.append(ratios)
                dates.append(day)
        prev_close = last_close
    if len(pools) < MIN_SESSIONS:
        raise TimeMachineError(f"only {len(pools)} full sessions (need {MIN_SESSIONS})")
    return np.stack(pools), dates


def _first_hits(side, opens, highs, lows, stop, target):
    """Per-run first slot index where stop / target hit (inf = never)."""
    if side == "long":
        stop_hit = (lows <= stop) | (opens <= stop)
        tgt_hit = (highs >= target) | (opens >= target)
    else:
        stop_hit = (highs >= stop) | (opens >= stop)
        tgt_hit = (lows <= target) | (opens <= target)
    big = np.iinfo(np.int32).max
    first_stop = np.where(stop_hit.any(axis=1), stop_hit.argmax(axis=1), big)
    first_tgt = np.where(tgt_hit.any(axis=1), tgt_hit.argmax(axis=1), big)
    return first_stop, first_tgt, big


def _outcomes(side, entry, stop, target, opens, highs, lows, closes):
    risk = abs(entry - stop)
    r_target = abs(target - entry) / risk
    first_stop, first_tgt, big = _first_hits(side, opens, highs, lows, stop, target)
    is_stop = (first_stop <= first_tgt) & (first_stop != big)   # same bar -> stop
    is_tgt = (first_tgt < first_stop)
    neither = ~is_stop & ~is_tgt
    end = closes[:, -1]
    drift = (end - entry) if side == "long" else (entry - end)
    r = np.where(is_tgt, r_target, np.where(is_stop, -1.0, drift / risk))
    green = is_tgt | (neither & (drift > 0))
    return is_tgt, is_stop, neither, green, r, end, r_target


def verdict(p_target: float, p_stop: float, r_target: float) -> str:
    decided = p_target + p_stop
    if decided <= 0:
        return "No replay reached the stop or the target"
    share = p_target / decided
    breakeven = 1.0 / (1.0 + r_target)
    diff = (share - breakeven) * 100.0
    if abs(diff) < 5:
        return "Close to a coin flip" if abs(r_target - 1.0) < 0.05 else "Close to break-even"
    side = "the target" if diff > 0 else "the stop"
    return ("Leans toward " if abs(diff) < 15 else "Strongly toward ") + side


def replay(
    df5: pd.DataFrame,
    *,
    entry: float,
    stop: float,
    target: float,
    at: datetime,
    side: str = "long",
    until: time | None = None,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    jitter: int = 2,
) -> dict:
    """Odds of target-first / stop-first / neither by `until` (default: the close)."""
    side = side.lower()
    if side not in ("long", "short"):
        raise TimeMachineError("side must be long or short")
    if side == "long" and not (stop < entry < target):
        raise TimeMachineError("a long needs stop < entry < target")
    if side == "short" and not (target < entry < stop):
        raise TimeMachineError("a short needs target < entry < stop")
    at = at.astimezone(NY)
    start = slot_of(at)
    end_slot = SLOTS if until is None else min(SLOTS, slot_of(until) + 1)
    if at.date() in early_closes(at.year):
        end_slot = min(end_slot, slot_of(time(13, 0)))
    if not (0 <= start < end_slot):
        raise TimeMachineError("the entry time is outside the session")

    pool, dates = session_pool(df5, before=at)
    steps = end_slot - start
    rng = np.random.default_rng(seed)
    sess = rng.integers(0, len(pool), size=(runs, steps))
    slots = np.clip(np.arange(start, end_slot)[None, :] + rng.integers(-jitter, jitter + 1, size=(runs, steps)),
                    0, SLOTS - 1)
    ratios = pool[sess, slots]                                   # (runs, steps, 4)
    closes = entry * np.cumprod(ratios[:, :, 3], axis=1)
    prev = np.concatenate([np.full((runs, 1), entry), closes[:, :-1]], axis=1)
    opens, highs, lows = prev * ratios[:, :, 0], prev * ratios[:, :, 1], prev * ratios[:, :, 2]
    is_tgt, is_stop, neither, green, r, end_px, r_target = _outcomes(
        side, entry, stop, target, opens, highs, lows, closes)

    # History: the same % levels on every past session from the same slot.
    hist = {"target": 0, "stop": 0, "neither": 0}
    stop_pct, tgt_pct = stop / entry, target / entry
    for ratios_s in pool:
        c = np.cumprod(np.r_[1.0, ratios_s[:, 3]])               # relative closes, c[k] = close of slot k-1
        base = c[start]                                          # price at the entry slot's open
        seg = ratios_s[start:end_slot]
        cl = c[start + 1:end_slot + 1] / base
        pv = np.r_[1.0, cl[:-1]]
        o, h, l = pv * seg[:, 0], pv * seg[:, 1], pv * seg[:, 2]
        t_, s_, n_, *_ = _outcomes(side, 1.0, stop_pct, tgt_pct, o[None], h[None], l[None], cl[None])
        hist["target" if t_[0] else "stop" if s_[0] else "neither"] += 1

    p_t, p_s, p_n = (float(x.mean()) for x in (is_tgt, is_stop, neither))
    return {
        "runs": runs,
        "seed": seed,
        "sessions": len(pool),
        "pool_from": str(dates[0]),
        "pool_to": str(dates[-1]),
        "side": side,
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "target": round(target, 4),
        "r_target": round(r_target, 2),
        "from_slot": start,
        "until_slot": end_slot - 1,
        "p_target": round(p_t, 4),
        "p_stop": round(p_s, 4),
        "p_neither": round(p_n, 4),
        "p_green": round(float(green.mean()), 4),
        "avg_r": round(float(r.mean()), 3),
        "close_p10": round(float(np.percentile(end_px, 10)), 2),
        "close_p50": round(float(np.percentile(end_px, 50)), 2),
        "close_p90": round(float(np.percentile(end_px, 90)), 2),
        "history": hist,
        "verdict": verdict(p_t, p_s, r_target),
        "neither_note": p_n > 0.40,
    }


def format_line(res: dict) -> str:
    """One alert line: 'Time Machine 2,000x/38d: TP 31% · SL 48% · open 21% → Leans toward the stop'."""
    return (
        f"Time Machine {res['runs']:,}x/{res['sessions']}d: "
        f"TP {res['p_target']:.0%} · SL {res['p_stop']:.0%} · open {res['p_neither']:.0%}"
        f" · avg {res['avg_r']:+.2f}R → {res['verdict']}"
    )
