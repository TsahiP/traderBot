"""Analyst step 5 - execution plan: stop, sizing, TP1/TP2 and R:R, all in code.

Long-only. The stop goes below the nearest demand block and below equilibrium
(the lower of the two), shaded a touch under the next round number so it does
not sit on a stop-hunt level. TP1 is the edge of the nearest supply block (half
size out), TP2 the next structural target (top of the dealing range). Numbers
here are copied verbatim into the reply; the LLM never recomputes them.
"""
from __future__ import annotations

import math

import risk

DEFAULT_MAX_POSITION_PCT = 25.0  # position cap when an account size is given
ATR_STOP_BUFFER = 0.25      # stop sits a quarter ATR under the level
MAX_STOP_ATR_MULT = 4.0     # stop farther than this many ATRs -> quality flag
ROUND_SHADE = 0.10          # < $200: park at X.90 just under the $X.00 level
EXPENSIVE_PRICE = 200.0     # $200+: the dime-grid stops mattering
EXPENSIVE_SHADE = 1.00      # $200+: shade whole dollars instead
TP1_HALF = 0.5              # half the size comes off at TP1


def round_break(raw: float, price: float) -> float:
    """Shade `raw` to sit just below a round number (stop-hunt guard).

    < $200: park at X.90 just under the highest $X.00 boundary <= raw.
    >= $200: the dime-grid is noise; if raw is already within a shade of the
    next round dollar it is kept, and a final stop always drops at least one
    dollar below its own level.
    """
    shade = EXPENSIVE_SHADE if price >= EXPENSIVE_PRICE else ROUND_SHADE
    boundary_above = math.ceil(raw)
    below_next = boundary_above - raw
    keep_window = ROUND_SHADE + 0.05 if shade == ROUND_SHADE else 0.05
    if 0 < below_next <= keep_window:  # already just under a round level
        return round(raw, 2)
    return round(math.floor(raw) - shade, 2)


def position_size(entry: float, stop: float, risk_usd: float, account: dict | None = None) -> dict:
    """fixed-fraction sizing: shares = floor(risk_usd / risk_per_share).

    With `account` ({equity, cash?, max_position_pct?, cash_buffer_pct?}) the
    size is also capped by the position cap and free cash (risk.size_position),
    and `limited_by` names the limit that decided it.
    """
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return {"shares": 0, "risk_per_share": risk_per_share,
                "risk_usd_actual": 0.0, "position_value": 0.0, "limited_by": "stop"}
    if account and account.get("equity"):
        equity = float(account["equity"])
        shares, limited_by = risk.size_position(
            entry=entry, stop=stop, max_loss_usd=risk_usd, equity=equity,
            cash=float(account.get("cash", equity)),
            max_position_pct=account.get("max_position_pct", DEFAULT_MAX_POSITION_PCT),
            cash_buffer_pct=account.get("cash_buffer_pct", 0.0),
        )
    else:
        shares, limited_by = int(risk_usd // risk_per_share), "max_loss"
    return {
        "shares": shares,
        "risk_per_share": round(risk_per_share, 4),
        "risk_usd_actual": round(shares * risk_per_share, 2),
        "position_value": round(shares * entry, 2),
        "limited_by": limited_by,
    }


def build_trade_plan(price: float, smc: dict, atr: float, risk_usd: float,
                     holding: dict | None = None, account: dict | None = None) -> dict:
    """price + snapshot_smc(1D) + ATR -> the step-5 execution block."""
    price = float(price)
    smc = smc if isinstance(smc, dict) else {}
    atr = float(atr) if atr and atr > 0 else 0.0
    flags: list[str] = []
    buffer = atr * ATR_STOP_BUFFER if atr else price * 0.01

    range_ = smc.get("range") if smc.get("range") else None
    demand = smc.get("demand_below")
    supply = smc.get("supply_above")

    # ---- stop basis: lowest of (demand block, equilibrium below price)
    bases: list[tuple[str, float]] = []
    if isinstance(demand, dict) and demand.get("lo"):
        bases.append(("order_block", float(demand["lo"])))
    if range_:
        mid = float(range_.get("mid", 0.0))
        if 0 < mid < price:
            bases.append(("equilibrium", mid))
        elif not bases and float(range_.get("lo", 0.0)) < price:
            bases.append(("swing_low", float(range_["lo"])))
    below = [(label, lvl) for label, lvl in bases if lvl < price]
    if below:
        raw = min(lvl for _, lvl in below) - buffer
        stop_basis = "+".join(sorted({label for label, _ in below}))
    else:
        raw = price - 2.0 * atr if atr else price * 0.96
        stop_basis = "atr_fallback"
        flags.append("no_structure")

    stop = round_break(raw, price)
    if stop >= price:
        stop = round(price - max(atr, price * 0.01), 2)
        flags.append("stop_capped")

    stop_distance = price - stop
    sizing = position_size(price, stop, risk_usd or 0.0, account)
    if sizing["limited_by"] not in ("max_loss", "stop"):
        flags.append(f"size_capped_by_{sizing['limited_by']}")

    # ---- targets
    tp1 = tp2 = None
    if isinstance(supply, dict) and supply.get("hi") is not None:
        tp1 = float(supply["hi"])
        if range_ and float(range_.get("hi", 0.0)) > tp1:
            tp2 = float(range_["hi"])
    elif range_:
        tp1 = float(range_["hi"])
        flags.append("no_supply_block")
    else:
        flags.append("no_structural_target")

    risk_per_share = sizing["risk_per_share"]
    with_risk = risk_per_share > 0
    rr1 = round((tp1 - price) / risk_per_share, 2) if tp1 is not None and with_risk else None
    rr2 = round((tp2 - price) / risk_per_share, 2) if tp2 is not None and with_risk else None
    combined_rr = (
        round(TP1_HALF * (rr1 or 0.0) + (1 - TP1_HALF) * (rr2 or 0.0), 2)
        if (tp1 is not None and tp2 is not None and with_risk) else None
    )
    if rr1 is not None and rr1 < 1.0:
        flags.append("tp1_rr_below_1")
    stop_atr = round(stop_distance / atr, 2) if atr else None
    if stop_atr is not None and stop_atr > MAX_STOP_ATR_MULT:
        flags.append("stop_far_atr")

    plan: dict = {
        "ok": True,
        "mode": "holding" if holding else "cash_entry",
        "entry": round(price, 4),
        "stop": stop,
        "stop_basis": stop_basis,
        "round_break": True,
        "risk_per_share": risk_per_share,
        "atr_atr_multiple": stop_atr,
        "tp1": {"price": tp1, "share_pct": 50, "rr": rr1},
        "tp2": {"price": tp2, "share_pct": 50, "rr": rr2},
        "combined_rr": combined_rr,
        "breakeven_after_tp1": True,
        **sizing,
        "flags": flags,
    }

    if holding:
        try:
            qty = float(holding.get("qty") or 0.0)
            avg = float(holding.get("avg_price") or 0.0)
        except (TypeError, ValueError):
            qty = avg = 0.0
        if qty > 0 and avg > 0:
            value = qty * avg
            pnl = (price - avg) * qty
            plan["holding"] = {
                "qty": qty,
                "avg_price": avg,
                "pnl_usd": round(pnl, 2),
                "pnl_pct": round(pnl / value * 100.0 if value else 0.0, 2),
                "stop_vs_avg_pct": round((avg - stop) / avg * 100.0 if avg else 0.0, 2),
                "stop_breakeven_or_better": stop >= avg,
                "plan_only_if_trigger": True,
            }
    return plan
