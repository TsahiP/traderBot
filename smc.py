"""Smart Money Concepts overlays for alert chart images (detection only)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

Side = Literal["bull", "bear"]
ZoneKind = Literal["order_block", "fvg"]
LabelKind = Literal["BOS", "CHoCH"]

LIQUIDITY_TOLERANCE = 0.0005  # 0.05%
SWING_RADIUS = 2
MAX_OB_PER_SIDE = 2
MAX_FVG_PER_SIDE = 2
MAX_STRUCTURE_LABELS = 4
MAX_LIQUIDITY = 2


@dataclass(frozen=True)
class PriceZone:
    kind: ZoneKind
    side: Side
    x0: int
    x1: int
    y_lo: float
    y_hi: float


@dataclass(frozen=True)
class StructureMark:
    kind: LabelKind
    side: Side
    bar: int
    price: float


@dataclass(frozen=True)
class LiquidityMark:
    side: Side
    level: float
    pool_bar: int
    sweep_bar: int | None


@dataclass(frozen=True)
class SmcOverlay:
    zones: list[PriceZone]
    structure: list[StructureMark]
    liquidity: list[LiquidityMark]


def default_smc_flags() -> dict:
    return {"zones": False, "structure": False, "liquidity": False}


def clean_smc_flags(raw) -> dict:
    incoming = raw if isinstance(raw, dict) else {}
    return {
        "zones": bool(incoming.get("zones", False)),
        "structure": bool(incoming.get("structure", False)),
        "liquidity": bool(incoming.get("liquidity", False)),
    }


def _swing_highs_lows(df: pd.DataFrame) -> tuple[list[int], list[int]]:
    n = len(df)
    highs: list[int] = []
    lows: list[int] = []
    if n < 2 * SWING_RADIUS + 1:
        return highs, lows
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    for i in range(SWING_RADIUS, n - SWING_RADIUS):
        left_h = h.iloc[i - SWING_RADIUS : i]
        right_h = h.iloc[i + 1 : i + SWING_RADIUS + 1]
        if h.iloc[i] > left_h.max() and h.iloc[i] > right_h.max():
            highs.append(i)
        left_l = l.iloc[i - SWING_RADIUS : i]
        right_l = l.iloc[i + 1 : i + SWING_RADIUS + 1]
        if l.iloc[i] < left_l.min() and l.iloc[i] < right_l.min():
            lows.append(i)
    return highs, lows


def _mitigation_end(df: pd.DataFrame, x0: int, y_lo: float, y_hi: float, side: Side) -> int:
    n = len(df)
    lo = df["Low"].astype(float)
    hi = df["High"].astype(float)
    for j in range(x0 + 1, n):
        if side == "bull":
            if lo.iloc[j] <= y_lo:
                return j
        else:
            if hi.iloc[j] >= y_hi:
                return j
    return n - 1


def _find_fvgs(df: pd.DataFrame, vis_start: int) -> list[PriceZone]:
    out: list[PriceZone] = []
    n = len(df)
    lo = df["Low"].astype(float)
    hi = df["High"].astype(float)
    for i in range(2, n):
        if lo.iloc[i] > hi.iloc[i - 2]:
            y_lo, y_hi = float(hi.iloc[i - 2]), float(lo.iloc[i])
            x1 = _mitigation_end(df, i - 2, y_lo, y_hi, "bull")
            if x1 >= vis_start or i - 2 >= vis_start:
                out.append(PriceZone("fvg", "bull", i - 2, x1, y_lo, y_hi))
        if hi.iloc[i] < lo.iloc[i - 2]:
            y_lo, y_hi = float(hi.iloc[i]), float(lo.iloc[i - 2])
            x1 = _mitigation_end(df, i - 2, y_lo, y_hi, "bear")
            if x1 >= vis_start or i - 2 >= vis_start:
                out.append(PriceZone("fvg", "bear", i - 2, x1, y_lo, y_hi))
    return out


def _last_opposite_bar(df: pd.DataFrame, break_bar: int, direction: Side) -> int | None:
    open_ = df["Open"].astype(float)
    close = df["Close"].astype(float)
    for j in range(break_bar - 1, -1, -1):
        o, c = float(open_.iloc[j]), float(close.iloc[j])
        if direction == "bull" and c < o:
            return j
        if direction == "bear" and c > o:
            return j
    return None


def _structure_and_obs(
    df: pd.DataFrame, swing_highs: list[int], swing_lows: list[int], vis_start: int
) -> tuple[list[StructureMark], list[PriceZone]]:
    n = len(df)
    close = df["Close"].astype(float)
    open_ = df["Open"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    structure: list[StructureMark] = []
    obs: list[PriceZone] = []
    trend: Side | None = None

    def last_swing_before(indices: list[int], bar: int) -> int | None:
        candidates = [i for i in indices if i < bar]
        return candidates[-1] if candidates else None

    for i in range(1, n):
        sh = last_swing_before(swing_highs, i)
        sl = last_swing_before(swing_lows, i)
        c = float(close.iloc[i])

        broke_up = sh is not None and c > float(high.iloc[sh])
        broke_down = sl is not None and c < float(low.iloc[sl])

        if broke_up:
            kind: LabelKind = "CHoCH" if trend == "bear" else "BOS"
            trend = "bull"
            if i >= vis_start:
                structure.append(StructureMark(kind, "bull", i, c))
            ob_i = _last_opposite_bar(df, i, "bull")
            if ob_i is not None:
                y_lo = min(float(open_.iloc[ob_i]), float(close.iloc[ob_i]))
                y_hi = max(float(open_.iloc[ob_i]), float(close.iloc[ob_i]))
                x1 = _mitigation_end(df, ob_i, y_lo, y_hi, "bull")
                if ob_i >= vis_start or x1 >= vis_start:
                    obs.append(PriceZone("order_block", "bull", ob_i, x1, y_lo, y_hi))

        elif broke_down:
            kind = "CHoCH" if trend == "bull" else "BOS"
            trend = "bear"
            if i >= vis_start:
                structure.append(StructureMark(kind, "bear", i, c))
            ob_i = _last_opposite_bar(df, i, "bear")
            if ob_i is not None:
                y_lo = min(float(open_.iloc[ob_i]), float(close.iloc[ob_i]))
                y_hi = max(float(open_.iloc[ob_i]), float(close.iloc[ob_i]))
                x1 = _mitigation_end(df, ob_i, y_lo, y_hi, "bear")
                if ob_i >= vis_start or x1 >= vis_start:
                    obs.append(PriceZone("order_block", "bear", ob_i, x1, y_lo, y_hi))

    return structure, obs


def _equal_level_clusters(indices: list[int], level_at: list[float]) -> list[tuple[int, float]]:
    """Return (latest bar in cluster, average level) for groups of 2+ swings."""
    clusters: list[tuple[int, float]] = []
    n_idx = len(indices)
    for a in range(n_idx):
        group = [indices[a]]
        levels = [level_at[a]]
        for b in range(a + 1, n_idx):
            mid = (levels[0] + level_at[b]) / 2
            if mid and abs(level_at[b] - levels[0]) / abs(mid) <= LIQUIDITY_TOLERANCE:
                group.append(indices[b])
                levels.append(level_at[b])
        if len(group) >= 2:
            clusters.append((max(group), sum(levels) / len(levels)))
    # dedupe by latest bar
    by_bar: dict[int, float] = {}
    for bar, lvl in clusters:
        by_bar[bar] = lvl
    return list(by_bar.items())


def _liquidity(
    df: pd.DataFrame, swing_highs: list[int], swing_lows: list[int], vis_start: int
) -> list[LiquidityMark]:
    close = df["Close"].astype(float)
    hi = df["High"].astype(float)
    lo = df["Low"].astype(float)
    marks: list[LiquidityMark] = []

    high_levels = [float(df["High"].iloc[i]) for i in swing_highs]
    low_levels = [float(df["Low"].iloc[i]) for i in swing_lows]

    for pool_bar, level in _equal_level_clusters(swing_highs, high_levels):
        sweep = _find_sweep_above(df, pool_bar + 1, level, close, hi)
        if pool_bar >= vis_start or (sweep is not None and sweep >= vis_start):
            marks.append(LiquidityMark("bear", level, pool_bar, sweep))

    for pool_bar, level in _equal_level_clusters(swing_lows, low_levels):
        sweep = _find_sweep_below(df, pool_bar + 1, level, close, lo)
        if pool_bar >= vis_start or (sweep is not None and sweep >= vis_start):
            marks.append(LiquidityMark("bull", level, pool_bar, sweep))

    return marks


def _find_sweep_above(df, start: int, level: float, close, hi) -> int | None:
    n = len(df)
    for j in range(start, n):
        if float(hi.iloc[j]) > level and float(close.iloc[j]) < level:
            return j
    return None


def _find_sweep_below(df, start: int, level: float, close, lo) -> int | None:
    n = len(df)
    for j in range(start, n):
        if float(lo.iloc[j]) < level and float(close.iloc[j]) > level:
            return j
    return None


def _cap_zones(zones: list[PriceZone]) -> list[PriceZone]:
    out: list[PriceZone] = []
    for kind, cap in (("order_block", MAX_OB_PER_SIDE), ("fvg", MAX_FVG_PER_SIDE)):
        for side in ("bull", "bear"):
            items = [z for z in zones if z.kind == kind and z.side == side]
            out.extend(sorted(items, key=lambda z: z.x0)[-cap:])
    return out


def _to_window_coords(
    zones: list[PriceZone],
    structure: list[StructureMark],
    liquidity: list[LiquidityMark],
    vis_start: int,
    n: int,
) -> SmcOverlay:
    def vis(bar: int) -> bool:
        return bar >= vis_start

    zones_vis = [
        PriceZone(
            z.kind,
            z.side,
            z.x0 - vis_start,
            min(z.x1, n - 1) - vis_start,
            z.y_lo,
            z.y_hi,
        )
        for z in zones
        if z.x1 >= vis_start or z.x0 >= vis_start
    ]
    structure_vis = [
        StructureMark(m.kind, m.side, m.bar - vis_start, m.price) for m in structure if vis(m.bar)
    ]
    liquidity_vis: list[LiquidityMark] = []
    for m in liquidity:
        if not (vis(m.pool_bar) or (m.sweep_bar is not None and vis(m.sweep_bar))):
            continue
        sweep = m.sweep_bar - vis_start if m.sweep_bar is not None and vis(m.sweep_bar) else None
        liquidity_vis.append(
            LiquidityMark(m.side, m.level, m.pool_bar - vis_start, sweep)
        )
    return SmcOverlay(zones_vis, structure_vis, liquidity_vis)


def detect_smc(df: pd.DataFrame, window: int = 30, flags: dict | None = None) -> SmcOverlay:
    """Run SMC on full history; return marks in coordinates of the last `window` bars."""
    flags = clean_smc_flags(flags or {})
    if not any(flags.values()) or df is None or len(df) < 5:
        return SmcOverlay([], [], [])

    n = len(df)
    vis_start = max(0, n - window)
    work = df.reset_index(drop=True)
    swing_highs, swing_lows = _swing_highs_lows(work)

    zones: list[PriceZone] = []
    structure: list[StructureMark] = []
    liquidity: list[LiquidityMark] = []

    structure_all, obs_all = _structure_and_obs(work, swing_highs, swing_lows, vis_start)

    if flags.get("zones"):
        zones = _cap_zones(_find_fvgs(work, vis_start) + obs_all)
    if flags.get("structure"):
        structure = sorted(structure_all, key=lambda m: m.bar)[-MAX_STRUCTURE_LABELS:]
    if flags.get("liquidity"):
        liquidity = sorted(
            _liquidity(work, swing_highs, swing_lows, vis_start),
            key=lambda m: m.pool_bar,
        )[-MAX_LIQUIDITY:]

    return _to_window_coords(zones, structure, liquidity, vis_start, n)
