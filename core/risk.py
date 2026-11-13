"""Risk gate for live entries: every new position must pass these checks first.

Pure functions, no broker calls - livebot gathers the numbers and asks
`check_entry()` for a verdict. Exits are never gated: getting out of a
position is always allowed.

Checks (an entry is blocked when any fails):
  stop         no stop, or a stop on the wrong side of the entry
  loss         qty x |entry - stop| over max_loss_per_trade_usd
  reward_risk  |target - entry| / |entry - stop| under min_reward_risk
  daily_loss   max losses at the stops of today's entries + this one over max_daily_loss_usd
  trades       max_trades_per_day entries already placed today
  duplicate    this symbol was already entered today
  window       the NY clock is outside [no_entries_before, no_entries_after]
  position     the order would be over max_position_pct of equity
  cash         (long) the buy would leave less than cash_buffer_pct of equity in cash
  buying_power (short) the short would leave less than that buffer of buying power

Every function takes `side` ("long" by default, or "short"). A short's stop
sits above the entry and its target below; a short does not spend cash, so it
is measured against buying power instead.

A limit set to None (or 0 for the numeric ones) is switched off.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time


@dataclass(frozen=True)
class RiskLimits:
    max_loss_per_trade_usd: float | None = 200.0
    max_daily_loss_usd: float | None = 500.0
    max_trades_per_day: int | None = 3
    min_reward_risk: float | None = 1.5
    max_position_pct: float | None = 25.0
    cash_buffer_pct: float | None = 10.0
    no_entries_before: str | None = "09:45"   # NY time, HH:MM
    no_entries_after: str | None = "15:30"
    require_stop: bool = True


@dataclass
class Verdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return "PASS" if self.ok else "BLOCK: " + "; ".join(self.reasons)


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _on(limit) -> bool:
    return limit is not None and limit != 0


SIDES = ("long", "short")


def _side(side: str) -> str:
    if side not in SIDES:
        raise ValueError(f"side must be long or short, got {side!r}")
    return side


def risk_per_share(entry: float, stop: float, side: str = "long") -> float:
    """Loss a share at the stop; zero or negative when the stop is on the wrong side."""
    return entry - stop if _side(side) == "long" else stop - entry


def max_loss(qty: float, entry: float, stop: float | None, side: str = "long") -> float | None:
    """Dollar loss if a position of `qty` is stopped out (None without a stop)."""
    if stop is None:
        return None
    return round(qty * risk_per_share(entry, stop, side), 2)


def size_position(
    *,
    entry: float,
    stop: float,
    max_loss_usd: float | None,
    equity: float | None = None,
    cash: float | None = None,
    max_position_pct: float | None = None,
    cash_buffer_pct: float | None = None,
    max_shares: int | None = None,
    side: str = "long",
    buying_power: float | None = None,
) -> tuple[int, str]:
    """Whole shares that fit every limit, and which limit decided it.

    Limits: max loss at the stop, position cap (% of equity), cash (long) or
    buying power (short) left after the buffer, and an absolute share cap. A
    limit that is None/0 is skipped. 0 shares with limited_by 'stop' means the
    stop is on the wrong side of the entry.
    """
    per_share = risk_per_share(entry, stop, side)
    if entry <= 0 or per_share <= 0:
        return 0, "stop"
    caps: list[tuple[str, float]] = []
    if _on(max_loss_usd):
        caps.append(("max_loss", max_loss_usd / per_share))
    if equity and _on(max_position_pct):
        caps.append(("position_cap", equity * max_position_pct / 100.0 / entry))
    funds, funds_label = (cash, "cash") if side == "long" else (buying_power, "buying_power")
    if funds is not None and equity:
        free = funds - equity * (cash_buffer_pct or 0.0) / 100.0
        caps.append((funds_label, max(free, 0.0) / entry))
    if _on(max_shares):
        caps.append(("max_shares", float(max_shares)))
    if not caps:
        return 0, "no_limits"
    label, shares = min(caps, key=lambda c: c[1])
    return max(int(shares + 1e-9), 0), label


def check_entry(
    *,
    symbol: str,
    qty: int,
    entry: float,
    stop: float | None,
    target: float | None,
    now_ny: datetime,
    equity: float,
    cash: float,
    entries_today: list[dict],
    limits: RiskLimits,
    side: str = "long",
    buying_power: float | None = None,
) -> Verdict:
    """Decide whether an entry may be sent. `entries_today` rows carry
    `symbol` and `max_loss` (the loss at the stop when it was placed).
    A short is checked against `buying_power` (falls back to `cash`)."""
    long = _side(side) == "long"
    reasons: list[str] = []

    if qty <= 0:
        reasons.append(f"qty {qty} is not positive")
    if entry <= 0:
        reasons.append(f"entry {entry} is not positive")

    per_share = None
    if stop is None:
        if limits.require_stop:
            reasons.append("stop: no stop on the order")
    elif risk_per_share(entry, stop, side) <= 0:
        where = "below" if long else "above"
        reasons.append(f"stop: {stop:.2f} is not {where} the entry {entry:.2f}")
    else:
        per_share = risk_per_share(entry, stop, side)

    loss = max_loss(qty, entry, stop, side) if per_share else None
    if loss is not None and _on(limits.max_loss_per_trade_usd) and loss > limits.max_loss_per_trade_usd:
        reasons.append(
            f"loss: ${loss:.2f} at the stop is over the ${limits.max_loss_per_trade_usd:.2f} per-trade limit"
        )

    if per_share and _on(limits.min_reward_risk):
        reward = None if target is None else (target - entry if long else entry - target)
        if reward is None or reward <= 0:
            reasons.append(f"reward_risk: no target {'above' if long else 'below'} the entry")
        else:
            rr = reward / per_share
            if rr < limits.min_reward_risk:
                reasons.append(f"reward_risk: {rr:.2f} is under the {limits.min_reward_risk:.2f} minimum")

    if loss is not None and _on(limits.max_daily_loss_usd):
        used = sum(float(e.get("max_loss") or 0.0) for e in entries_today)
        if used + loss > limits.max_daily_loss_usd:
            reasons.append(
                f"daily_loss: ${used:.2f} used today + ${loss:.2f} is over the ${limits.max_daily_loss_usd:.2f} day limit"
            )

    if _on(limits.max_trades_per_day) and len(entries_today) >= limits.max_trades_per_day:
        reasons.append(f"trades: {len(entries_today)} entries today, limit {limits.max_trades_per_day}")

    if any(e.get("symbol") == symbol for e in entries_today):
        reasons.append(f"duplicate: {symbol} was already entered today")

    clock = now_ny.time()
    if limits.no_entries_before and clock < _hhmm(limits.no_entries_before):
        reasons.append(f"window: {clock:%H:%M} NY is before {limits.no_entries_before}")
    if limits.no_entries_after and clock > _hhmm(limits.no_entries_after):
        reasons.append(f"window: {clock:%H:%M} NY is after {limits.no_entries_after}")

    value = qty * entry
    if equity > 0 and _on(limits.max_position_pct):
        pct = value / equity * 100.0
        if pct > limits.max_position_pct:
            reasons.append(f"position: {pct:.1f}% of equity is over the {limits.max_position_pct:.1f}% cap")
    if equity > 0 and limits.cash_buffer_pct is not None:
        floor = equity * limits.cash_buffer_pct / 100.0
        if long and cash - value < floor:
            reasons.append(
                f"cash: ${cash - value:,.2f} left after the buy is under the ${floor:,.2f} buffer"
            )
        funds = cash if buying_power is None else buying_power
        if not long and funds - value < floor:
            reasons.append(
                f"buying_power: ${funds - value:,.2f} left after the short is under the ${floor:,.2f} buffer"
            )

    return Verdict(ok=not reasons, reasons=reasons)
