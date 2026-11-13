"""Runs and setups: what each run is watching, and the daily strategy evaluation.

A run (config `runs[]`) is a strategy + timeframe + universe + its own risk
budget. For each (run, symbol, trading day) there is one setup moving through

    IDLE -> ARMED -> WAITING -> TRIGGERED -> (PROPOSED) -> ENTERED -> MANAGING -> EXITED
                                     \\-> EXPIRED / BLOCKED

`transition` refuses illegal moves, so a setup triggers at most once a day
(the Entry Timer rule) and a finished one stays finished. Staying in the same
state is how live details (distance to the level, the sentence) update.

`evaluate_daily` runs a daily `strategy` entry (phase 1: swing_sma, the SMA
crossover livebot traded) on completed bars and returns intents:
- an entry only on a signal from the LAST completed bar, acted on once
  (`acted_signal_bar`) - a late entry is a different trade;
- an exit whenever the latest signal opposes the open position and the run
  exits on opposite signals, however old the signal is;
- a reversal (exit, then entry the other way) when shorts are allowed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

import pandas as pd

import core.strategies as strategies
from core.strategy import latest_signal


class SetupState(str, Enum):
    IDLE = "IDLE"
    ARMED = "ARMED"
    WAITING = "WAITING"
    TRIGGERED = "TRIGGERED"
    PROPOSED = "PROPOSED"
    ENTERED = "ENTERED"
    MANAGING = "MANAGING"
    EXITED = "EXITED"
    EXPIRED = "EXPIRED"
    BLOCKED = "BLOCKED"


S = SetupState
TRANSITIONS: dict[SetupState, frozenset] = {
    S.IDLE: frozenset({S.ARMED, S.WAITING, S.TRIGGERED, S.EXPIRED}),
    S.ARMED: frozenset({S.IDLE, S.WAITING, S.TRIGGERED, S.EXPIRED}),
    S.WAITING: frozenset({S.IDLE, S.ARMED, S.TRIGGERED, S.EXPIRED}),
    S.TRIGGERED: frozenset({S.PROPOSED, S.ENTERED, S.BLOCKED, S.EXPIRED}),
    S.PROPOSED: frozenset({S.ENTERED, S.EXPIRED, S.BLOCKED}),
    S.ENTERED: frozenset({S.MANAGING, S.EXITED, S.BLOCKED}),   # BLOCKED: the entry order died unfilled
    S.MANAGING: frozenset({S.EXITED}),
    S.EXITED: frozenset(),
    S.EXPIRED: frozenset(),
    S.BLOCKED: frozenset(),
}
# Bars a strategy needs before its signals mean anything (+ WARMUP_PAD).
WARMUP_PARAM = {"sma_crossover": "slow", "rsi_mean_reversion": "rsi_period"}
WARMUP_PAD = 5


class IllegalTransition(ValueError):
    pass


@dataclass(frozen=True)
class Setup:
    run_id: str
    symbol: str
    day: str
    state: SetupState
    side: str | None = None
    level: float | None = None
    distance_pct: float | None = None
    sentence: str | None = None
    detail: dict = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict) -> "Setup":
        return cls(run_id=row["run_id"], symbol=row["symbol"], day=row["day"],
                   state=SetupState(row["state"]), side=row.get("side"), level=row.get("level"),
                   distance_pct=row.get("distance_pct"), sentence=row.get("sentence"),
                   detail=row.get("detail") or {})

    def as_row(self) -> dict:
        return {"run_id": self.run_id, "symbol": self.symbol, "day": self.day,
                "state": self.state.value, "side": self.side, "level": self.level,
                "distance_pct": self.distance_pct, "sentence": self.sentence, "detail": self.detail}


def new_setup(run_id: str, symbol: str, day: str, state: SetupState = S.IDLE, **fields) -> Setup:
    return Setup(run_id=run_id, symbol=symbol, day=day, state=state, **fields)


def transition(setup: Setup, state: SetupState, **changes) -> Setup:
    if state is not setup.state and state not in TRANSITIONS[setup.state]:
        raise IllegalTransition(f"{setup.run_id}/{setup.symbol}: {setup.state.value} -> {state.value}")
    return replace(setup, state=state, **changes)


@dataclass(frozen=True)
class Run:
    id: str
    enabled: bool
    style: str
    timeframe: str
    symbols: tuple[str, ...] | None
    entry: dict
    exit: dict
    allow_short: bool
    min_score: float
    timemachine: bool
    risk: dict

    @classmethod
    def from_config(cls, raw: dict) -> "Run":
        symbols = raw.get("symbols")
        return cls(id=raw["id"], enabled=raw.get("enabled", True), style=raw["style"],
                   timeframe=raw["timeframe"], symbols=tuple(symbols) if symbols else None,
                   entry=dict(raw["entry"]), exit=dict(raw["exit"]),
                   allow_short=raw.get("allow_short", False), min_score=raw.get("min_score", 0),
                   timemachine=raw.get("timemachine", False), risk=dict(raw.get("risk") or {}))

    @property
    def exits_on_signal(self) -> bool:
        return bool(self.exit.get("exit_on_opposite_signal", False))


def runs_from_config(cfg: dict, enabled_only: bool = True) -> list[Run]:
    runs = [Run.from_config(r) for r in cfg["runs"]]
    return [r for r in runs if r.enabled] if enabled_only else runs


@dataclass(frozen=True)
class Intent:
    kind: str            # entry | exit
    run_id: str
    symbol: str
    side: str            # entry: the side to open; exit: the side being closed
    signal_bar: str
    ref_price: float
    reason: str


@dataclass(frozen=True)
class Evaluation:
    state: SetupState
    intents: list
    sentence: str
    signal_bar: str | None = None
    level: float | None = None
    distance_pct: float | None = None
    detail: dict = field(default_factory=dict)


def _warmup(run: Run) -> int:
    key = WARMUP_PARAM.get(run.entry.get("strategy"))
    return int(run.entry["params"][key]) + WARMUP_PAD if key else 20


def _describe(run: Run, df: pd.DataFrame) -> tuple[str, float | None, float | None, dict]:
    """(sentence, level, distance_pct, detail) for the UI."""
    close = float(df["Close"].iloc[-1])
    nonzero = df.loc[df["signal"] != 0]
    detail = {"close": close, "last_signal": int(nonzero["signal"].iloc[-1]) if len(nonzero) else 0,
              "last_signal_bar": str(nonzero.index[-1].date()) if len(nonzero) else None}
    if "sma_fast" in df and "sma_slow" in df:
        fast, slow = float(df["sma_fast"].iloc[-1]), float(df["sma_slow"].iloc[-1])
        gap = (fast - slow) / slow * 100
        p = run.entry["params"]
        detail.update(sma_fast=round(fast, 4), sma_slow=round(slow, 4))
        sentence = (f"SMA {p['fast']} is {abs(gap):.2f}% {'above' if gap > 0 else 'below'} "
                    f"SMA {p['slow']}")
        return sentence, round(slow, 4), round(gap, 4), detail
    sentence = (f"last signal {detail['last_signal']:+d} on {detail['last_signal_bar']}"
                if detail["last_signal"] else "no signal yet")
    return sentence, None, None, detail


def _cross_reason(run: Run, signal: int, bar: str, close: float) -> str:
    if run.entry.get("strategy") == "sma_crossover":
        p = run.entry["params"]
        word = "above" if signal > 0 else "below"
        return f"SMA {p['fast']} crossed {word} SMA {p['slow']} on {bar} (close {close:.2f})"
    return f"{run.entry.get('strategy')} signal {signal:+d} on {bar} (close {close:.2f})"


def evaluate_daily(run: Run, symbol: str, bars: pd.DataFrame, position_side: str | None = None,
                   acted_signal_bar: str | None = None) -> Evaluation:
    """Intents for one daily `strategy` run on completed bars (see module doc)."""
    if run.entry.get("kind") != "strategy" or run.timeframe != "1d":
        raise ValueError(f"{run.id}: evaluate_daily handles daily strategy runs only")
    need = _warmup(run)
    if len(bars) < need:
        return Evaluation(S.IDLE, [], f"warming up: {len(bars)} of {need} daily bars")

    df = strategies.run_strategy(run.entry["strategy"], bars, run.entry["params"])
    sentence, level, distance, detail = _describe(run, df)
    close = detail["close"]
    last_bar = str(df.index[-1].date())
    fresh = int(df["signal"].iloc[-1])
    latest = latest_signal(df)
    signal_bar = detail["last_signal_bar"]

    intents: list[Intent] = []
    if position_side is not None:
        opposite = -1 if position_side == "long" else 1
        if latest == opposite and run.exits_on_signal:
            intents.append(Intent("exit", run.id, symbol, position_side, signal_bar, close,
                                  _cross_reason(run, latest, signal_bar, close)))
        if not intents:
            exit_note = " - exits on an opposite signal" if run.exits_on_signal else ""
            return Evaluation(S.MANAGING, [], f"holding {position_side}; {sentence}{exit_note}",
                              signal_bar, level, distance, detail)

    flat_after = position_side is None or bool(intents)
    if flat_after and fresh != 0 and acted_signal_bar != last_bar:
        side = "long" if fresh > 0 else "short"
        if side != position_side and (side == "long" or run.allow_short):
            intents.append(Intent("entry", run.id, symbol, side, last_bar, close,
                                  _cross_reason(run, fresh, last_bar, close)))

    if any(i.kind == "entry" for i in intents):
        state = S.TRIGGERED
    elif position_side is not None:
        state = S.MANAGING
    else:
        state = S.WAITING
        sentence = f"{sentence}; entries only on a fresh cross"
    return Evaluation(state, intents, sentence, signal_bar, level, distance, detail)
