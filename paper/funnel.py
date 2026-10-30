"""The decision funnel: every entry intent goes through the same stages, and each
stage writes down what it saw, so the UI can show exactly why a trade was
taken or skipped.

    universe -> position -> news -> trigger -> score -> exit_plan -> timemachine
             -> llm -> asset -> size -> risk -> approval

Each stage returns {stage, status: pass|fail|skip|warn, value, reason, ms}.
The first `fail` stops the funnel and the rest are `skip` / "not reached".
Outcomes: `ready` (auto mode: the executor sends it), `proposed` (manual
mode: waits for Approve in the Paper desk), `blocked`, `vetoed` (the LLM,
phase 3). Exits never come here: getting out is never gated.

Pure: `FunnelContext` carries plain data (account, positions, today's
entries, the asset, bars), so the same intent and context always give the
same decision. `entry_at` is when the order will actually go out - the swing
pass decides at 16:05 and sends at the next morning's window - and the
entry-window check uses it.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

import market_calendar
import risk
from paper import exit_plan
from paper.runs import Intent, Run

STAGES = ("universe", "position", "news", "trigger", "score", "exit_plan", "timemachine",
          "llm", "asset", "size", "risk", "approval")


@dataclass
class FunnelContext:
    now_ny: datetime
    entry_at: datetime
    cfg: dict
    run: Run
    universe: set
    account: dict                       # equity, cash, buying_power
    positions: list                     # broker positions (all runs)
    pending_symbols: set                # symbols with an entry order / proposal in flight
    entries_today: list                 # all runs: [{symbol, max_loss}] for the entry day
    run_entries_today: list             # this run only
    open_risk_usd: float                # max loss at the stops of open trades
    short_exposure_usd: float           # notional of open shorts
    asset: dict                         # tradable, shortable, easy_to_borrow
    bars: pd.DataFrame | None
    price: float | None = None          # reference price (falls back to the intent's)
    or_high: float | None = None
    or_low: float | None = None


@dataclass
class Decision:
    run_id: str
    symbol: str
    side: str
    signal_bar: str
    ref_price: float
    outcome: str
    failed_stage: str | None
    stages: list
    plan: dict | None = None
    qty: int | None = None
    limited_by: str | None = None
    max_loss: float | None = None
    score: float | None = None
    llm_mode: str | None = None
    llm_verdict: str | None = None
    llm_would_block: bool | None = None

    def journal_fields(self, *, ts: str, config_hash: str, outcome: str | None = None) -> dict:
        """Keyword arguments for Journal.add_decision."""
        return {"ts": ts, "run_id": self.run_id, "symbol": self.symbol, "side": self.side,
                "outcome": outcome or self.outcome, "failed_stage": self.failed_stage,
                "score": self.score, "qty": self.qty, "max_loss": self.max_loss,
                "stages": self.stages, "plan": self.plan, "llm_mode": self.llm_mode,
                "llm_verdict": self.llm_verdict, "llm_would_block": self.llm_would_block,
                "config_hash": config_hash}

    def summary(self) -> str:
        if self.failed_stage:
            reason = next(s["reason"] for s in self.stages if s["stage"] == self.failed_stage)
            return f"{self.outcome} at {self.failed_stage}: {reason}"
        p = self.plan
        return (f"{self.outcome}: {self.side} {self.qty} {self.symbol} @ ~{p['entry']:.2f}, "
                f"stop {p['stop']:.2f}, target {p['target']:.2f}, max loss ${self.max_loss:.2f}")


class _Fail(Exception):
    def __init__(self, reason: str, value=None):
        super().__init__(reason)
        self.reason, self.value = reason, value


@dataclass
class _Runner:
    stages: list = field(default_factory=list)

    def run(self, name: str, fn) -> bool:
        """Run one stage; False when it failed."""
        t0 = _time.perf_counter()
        try:
            status, value, reason = fn()
        except _Fail as f:
            status, value, reason = "fail", f.value, f.reason
        self.stages.append({"stage": name, "status": status, "value": value, "reason": reason,
                            "ms": round((_time.perf_counter() - t0) * 1000, 3)})
        return status != "fail"

    def skip_rest(self) -> None:
        done = {s["stage"] for s in self.stages}
        for name in STAGES:
            if name not in done:
                self.stages.append({"stage": name, "status": "skip", "value": None,
                                    "reason": "not reached", "ms": 0.0})


def _shift_hhmm(value: str | None, hours: int) -> str | None:
    if not value:
        return value
    h, m = (int(x) for x in value.split(":"))
    return f"{h + hours:02d}:{m:02d}"


def _entry_window(cfg: dict, at: datetime) -> tuple[str | None, str | None]:
    """The NY entry window on `at`'s day; ends 3h sooner on a 1:00 p.m. close."""
    g = cfg["global_risk"]
    after = g["no_entries_after"]
    if at.date() in market_calendar.early_closes(at.year):
        after = _shift_hhmm(after, -3)
    return g["no_entries_before"], after


def run_funnel(intent: Intent, ctx: FunnelContext) -> Decision:
    if intent.kind != "entry":
        raise ValueError("only entries go through the funnel - exits are never gated")
    run, cfg, side, symbol = ctx.run, ctx.cfg, intent.side, intent.symbol
    price = float(ctx.price or intent.ref_price)
    g = cfg["global_risk"]
    state: dict = {}
    r = _Runner()

    def universe():
        if symbol not in ctx.universe:
            raise _Fail(f"{symbol} is not in today's universe for {run.id}")
        return "pass", symbol, f"{symbol} is in {run.id}'s universe"

    def position():
        held = next((p for p in ctx.positions if p["symbol"] == symbol), None)
        if held:
            raise _Fail(f"already holding {held['side']} {held['qty']:g} {symbol} (one position per symbol)")
        if symbol in ctx.pending_symbols:
            raise _Fail(f"an entry for {symbol} is already pending")
        return "pass", None, f"no position or pending entry in {symbol}"

    def news():
        return "skip", None, "news and fundamentals check comes in phase 3"

    def trigger():
        if side == "short" and not run.allow_short:
            raise _Fail(f"{run.id} does not allow short entries")
        value = {"signal_bar": intent.signal_bar, "ref_price": intent.ref_price}
        return "pass", value, intent.reason

    def score():
        return "skip", None, "confluence score comes in phase 3"

    def plan_stage():
        day = run.style == "day"
        out = exit_plan.plan(side, price, run.exit["stop"], run.exit["target_r"], bars=ctx.bars,
                             or_high=ctx.or_high, or_low=ctx.or_low,
                             now_ny=ctx.entry_at if day else None,
                             flat_by=g["flat_by"] if day else None)
        if isinstance(out, exit_plan.Refusal):
            raise _Fail(out.reason)
        state["plan"] = out
        return ("pass", out.as_dict(),
                f"stop {out.stop:.2f} ({out.stop_note}), target {out.target:.2f} ({out.target_r:g}R)")

    def timemachine():
        if not run.timemachine:
            return "skip", None, "off for this run"
        return "skip", None, "Time Machine check comes in phase 2"

    def llm():
        if cfg["llm_mode"] == "off":
            return "skip", None, "LLM is off"
        return "skip", None, f"LLM gate ({cfg['llm_mode']}) comes in phase 3"

    def asset():
        a = ctx.asset or {}
        if not a.get("tradable"):
            raise _Fail(f"{symbol} is not tradable at Alpaca", a)
        if side == "short" and not (a.get("shortable") and a.get("easy_to_borrow")):
            raise _Fail(f"{symbol} is not shortable and easy to borrow (no hard-to-borrow shorts)", a)
        return "pass", a, "tradable" + (", shortable, easy to borrow" if side == "short" else "")

    def size():
        p = state["plan"]
        acct = ctx.account
        qty, limited_by = risk.size_position(
            entry=price, stop=p.stop, max_loss_usd=run.risk.get("max_loss_per_trade_usd"),
            equity=acct["equity"], cash=acct["cash"], buying_power=acct.get("buying_power"),
            max_position_pct=run.risk.get("max_position_pct"), cash_buffer_pct=g["cash_buffer_pct"],
            side=side,
        )
        if qty <= 0:
            raise _Fail(f"0 shares fit (limited by {limited_by})", {"qty": 0, "limited_by": limited_by})
        state["qty"], state["limited_by"] = qty, limited_by
        state["max_loss"] = risk.max_loss(qty, price, p.stop, side)
        return ("pass", {"qty": qty, "limited_by": limited_by, "max_loss": state["max_loss"]},
                f"{qty} shares (limited by {limited_by}), max loss ${state['max_loss']:.2f}")

    def risk_stage():
        p, qty, loss = state["plan"], state["qty"], state["max_loss"]
        acct = ctx.account
        before, after = _entry_window(cfg, ctx.entry_at)
        common = dict(symbol=symbol, qty=qty, entry=price, stop=p.stop, target=p.target,
                      now_ny=ctx.entry_at, equity=acct["equity"], cash=acct["cash"],
                      side=side, buying_power=acct.get("buying_power"))
        run_limits = risk.RiskLimits(
            max_loss_per_trade_usd=run.risk.get("max_loss_per_trade_usd"),
            max_daily_loss_usd=run.risk.get("max_daily_loss_usd"),
            max_trades_per_day=run.risk.get("max_trades_per_day"),
            min_reward_risk=run.risk.get("min_reward_risk"),
            max_position_pct=run.risk.get("max_position_pct"),
            cash_buffer_pct=g["cash_buffer_pct"],
            no_entries_before=before, no_entries_after=after,
        )
        global_limits = risk.RiskLimits(
            max_loss_per_trade_usd=None, max_daily_loss_usd=g["max_daily_loss_usd"],
            max_trades_per_day=g["max_trades_per_day"], min_reward_risk=None,
            max_position_pct=None, cash_buffer_pct=None, no_entries_before=None,
            no_entries_after=None, require_stop=False,
        )
        run_v = risk.check_entry(entries_today=ctx.run_entries_today, limits=run_limits, **common)
        glob_v = risk.check_entry(entries_today=ctx.entries_today, limits=global_limits, **common)
        global_reasons = list(glob_v.reasons)
        open_count = len(ctx.positions) + len(ctx.pending_symbols)
        if g["max_open_positions"] and open_count >= g["max_open_positions"]:
            global_reasons.append(f"open_positions: {open_count} open, limit {g['max_open_positions']}")
        if g["max_open_risk_usd"] is not None and ctx.open_risk_usd + loss > g["max_open_risk_usd"]:
            global_reasons.append(f"open_risk: ${ctx.open_risk_usd:.2f} open + ${loss:.2f} is over "
                                  f"the ${g['max_open_risk_usd']:.2f} limit")
        cap = g["max_short_exposure_pct"]
        if side == "short" and cap is not None and acct["equity"] > 0:
            pct = (ctx.short_exposure_usd + qty * price) / acct["equity"] * 100
            if pct > cap:
                global_reasons.append(f"short_exposure: {pct:.1f}% of equity short is over the {cap:.1f}% cap")
        value = {"run": list(run_v.reasons), "global": global_reasons}
        reasons = [f"run {x}" for x in run_v.reasons] + [f"global {x}" for x in global_reasons]
        if reasons:
            raise _Fail("; ".join(reasons), value)
        return "pass", value, "run and global limits pass"

    def approval():
        if cfg["approval_mode"] == "manual":
            return "warn", "manual", "waiting for Approve in the Paper desk"
        return "pass", "auto", "auto: the bot sends it"

    steps = {"universe": universe, "position": position, "news": news, "trigger": trigger,
             "score": score, "exit_plan": plan_stage, "timemachine": timemachine, "llm": llm,
             "asset": asset, "size": size, "risk": risk_stage, "approval": approval}
    failed = None
    for name in STAGES:
        if not r.run(name, steps[name]):
            failed = name
            r.skip_rest()
            break

    plan = state.get("plan")
    if failed:
        outcome = "blocked"
    else:
        outcome = "proposed" if cfg["approval_mode"] == "manual" else "ready"
    return Decision(
        run_id=run.id, symbol=symbol, side=side, signal_bar=intent.signal_bar,
        ref_price=intent.ref_price, outcome=outcome, failed_stage=failed, stages=r.stages,
        plan=plan.as_dict() if plan else None,      # kept on a late block: the UI shows what it would have sent
        qty=state.get("qty"), limited_by=state.get("limited_by"), max_loss=state.get("max_loss"),
        llm_mode=cfg["llm_mode"],
    )
