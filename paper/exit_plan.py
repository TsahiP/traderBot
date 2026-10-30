"""Exit planner: the stop and the target, set before the entry (trader-exit's rules).

| rule      | long stop                         | short stop                        |
|-----------|-----------------------------------|-----------------------------------|
| or-mid    | opening-range midpoint            | opening-range midpoint            |
| or-far    | opening-range low                 | opening-range high                |
| atr:K     | entry - K x ATR14 (run timeframe) | entry + K x ATR14                 |
| pct:X     | entry x (1 - X%)                  | entry x (1 + X%)                  |
| structure | phase 3 (trade_plan demand/supply blocks)                             |

The stop rounds AWAY from the entry to the cent (down for a long, up for a
short); the target is entry +/- R x risk, rounded half-up. Refused, with the
reason: a stop on the wrong side, a risk under 0.05% of the price (too tight
to survive the spread), a target at or below zero, no data for the rule, or a
day entry at/after the flat-by time. A stop is never widened to make a plan pass.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, time
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

import pandas as pd

from engine import ATR_PERIOD, atr_series
from paper.config import ConfigError, parse_stop_rule

MIN_RISK_PCT = 0.05        # risk a share under this % of the entry is too tight
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Plan:
    side: str
    entry: float
    stop: float
    target: float
    risk_per_share: float
    target_r: float
    reward_risk: float
    rule: str
    stop_note: str
    flat_by: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Refusal:
    reason: str
    rule: str


def _cents(value: float, rounding) -> Decimal:
    return Decimal(str(round(value, 6))).quantize(CENT, rounding=rounding)


def _atr(bars: pd.DataFrame | None) -> float | None:
    if bars is None or len(bars) < ATR_PERIOD + 1:
        return None
    value = float(atr_series(bars, ATR_PERIOD).iloc[-1])
    return value if math.isfinite(value) and value > 0 else None


def _raw_stop(side: str, entry: float, kind: str, value: float | None, bars, or_high, or_low):
    """(stop before rounding, note) or (None, refusal reason)."""
    sign = -1 if side == "long" else 1
    if kind == "atr":
        atr = _atr(bars)
        if atr is None:
            return None, f"atr:{value:g} needs {ATR_PERIOD + 1}+ bars for the ATR"
        return entry + sign * value * atr, f"{value:g} x ATR{ATR_PERIOD} {atr:.2f}"
    if kind == "pct":
        return entry * (1 + sign * value / 100), f"{value:g}% from the entry"
    if kind in ("or-mid", "or-far"):
        if or_high is None or or_low is None or or_high <= or_low:
            return None, f"{kind} needs the opening range high and low"
        if kind == "or-mid":
            mid = (or_high + or_low) / 2
            return mid, f"opening-range midpoint ({or_high:.2f} + {or_low:.2f}) / 2"
        edge = or_low if side == "long" else or_high
        return edge, f"opening-range {'low' if side == 'long' else 'high'} {edge:.2f}"
    return None, "structure stops come with the swing_patterns run (phase 3)"


def plan(side: str, entry: float, rule: str, target_r: float, *, bars: pd.DataFrame | None = None,
         or_high: float | None = None, or_low: float | None = None,
         now_ny: datetime | None = None, flat_by: str | None = None) -> Plan | Refusal:
    if side not in ("long", "short"):
        return Refusal(f"side must be long or short, got {side!r}", rule)
    try:
        kind, value = parse_stop_rule(rule)
    except ConfigError as exc:
        return Refusal(str(exc), rule)
    if not entry or entry <= 0:
        return Refusal(f"entry {entry!r} is not a positive price", rule)
    if not target_r or target_r <= 0:
        return Refusal(f"target of {target_r!r}R is not above zero", rule)
    if now_ny is not None and flat_by:
        h, m = (int(x) for x in flat_by.split(":"))
        if now_ny.time() >= time(h, m):
            return Refusal(f"entry at {now_ny:%H:%M} is at/after the {flat_by} flat-by time", rule)

    raw, note = _raw_stop(side, entry, kind, value, bars, or_high, or_low)
    if raw is None:
        return Refusal(note, rule)
    e = Decimal(str(entry))
    stop = _cents(raw, ROUND_FLOOR if side == "long" else ROUND_CEILING)
    risk = e - stop if side == "long" else stop - e
    if risk <= 0:
        where = "below" if side == "long" else "above"
        return Refusal(f"stop {stop} is not {where} the entry {entry:.2f}", rule)
    if risk / e * 100 < Decimal(str(MIN_RISK_PCT)):
        return Refusal(f"stop {stop} is too tight: risk {risk} a share is under {MIN_RISK_PCT}% "
                       f"of the price", rule)
    move = Decimal(str(target_r)) * risk
    target = (e + move if side == "long" else e - move).quantize(CENT, rounding=ROUND_HALF_UP)
    if target <= 0:
        return Refusal(f"target {target} is not a positive price", rule)
    return Plan(
        side=side, entry=float(entry), stop=float(stop), target=float(target),
        risk_per_share=round(float(risk), 4), target_r=float(target_r),
        reward_risk=round(float(abs(target - e) / risk), 2), rule=rule,
        stop_note=note, flat_by=flat_by,
    )
