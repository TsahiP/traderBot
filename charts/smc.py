"""Smart Money Concepts, ported from LuxAlgo's "Smart Money Concepts" TradingView
indicator so the bot reads the same structure the user sees on the chart.

One bar-by-bar pass (`run_luxalgo`) reproduces the indicator's state machine:
swing structure (length 50) and internal structure (length 5) BOS / CHoCH,
internal order blocks (ATR filter, High/Low mitigation), EQH / EQL, fair value
gaps (auto threshold), Strong/Weak High/Low and Premium/Equilibrium/Discount
zones. `detect_smc` turns that into chart overlays; `snapshot_smc` into the
absolute-price dict the analyst reads.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np
import pandas as pd

Side = Literal["bull", "bear"]
Scope = Literal["swing", "internal"]
LabelKind = Literal["BOS", "CHoCH"]

# The user's LuxAlgo SMC inputs (Historical mode, Colored style).
LUXALGO_SETTINGS: dict = {
    "swing_length": 50,
    "internal_length": 5,
    "internal_confluence": False,
    "internal_obs": True,
    "internal_ob_count": 5,
    "swing_obs": False,
    "swing_ob_count": 5,
    "ob_filter": "atr",            # "atr" | "range" (cumulative mean range)
    "ob_mitigation": "highlow",    # "highlow" | "close"
    "eq_length": 3,
    "eq_threshold": 0.1,
    "fvg_auto_threshold": True,
    "fvg_extend": 5,
}

ATR_LENGTH = 200
MAX_STORED_OBS = 100
EQ_ZONE = (0.475, 0.525)       # equilibrium band as a fraction of the range
EDGE_ZONE = 0.05               # premium / discount band at each extreme
PRICE_EOL = 2                  # kept for callers that import it


@dataclass(frozen=True)
class StructureEvent:
    kind: LabelKind
    side: Side
    scope: Scope
    pivot_bar: int
    bar: int          # bar whose close broke the pivot
    price: float      # the broken pivot level


@dataclass(frozen=True)
class EqualLevel:
    side: Side        # "bear" = EQH (liquidity above), "bull" = EQL
    bar0: int
    level0: float
    bar1: int
    level1: float
    sweep_bar: int | None = None


@dataclass(frozen=True)
class PriceZone:
    kind: Literal["order_block", "fvg"]
    side: Side
    x0: int
    x1: int           # last bar the box spans (order blocks: the last bar)
    y_lo: float
    y_hi: float
    scope: Scope | None = None


@dataclass
class LuxResult:
    n: int
    structure: list[StructureEvent]
    equal_levels: list[EqualLevel]
    order_blocks: list[PriceZone]          # active, newest first, capped like the chart
    fvgs: list[PriceZone]                  # active, newest first
    swing_bias: Side | None
    internal_bias: Side | None
    top: float | None                      # trailing swing extreme (Strong/Weak High)
    top_bar: int | None
    bottom: float | None
    bottom_bar: int | None
    range_bar: int | None                  # left edge of the P/E/D zones
    range_bias: Side | None = None         # swing bias the Strong/Weak labels were drawn with
    settings: dict = field(default_factory=dict)

    @property
    def top_label(self) -> str:
        return "Strong High" if self.range_bias == "bear" else "Weak High"

    @property
    def bottom_label(self) -> str:
        return "Strong Low" if self.range_bias == "bull" else "Weak Low"

    def zones(self) -> dict | None:
        """LuxAlgo Premium / Equilibrium / Discount boxes."""
        if self.top is None or self.bottom is None:
            return None
        t, b = self.top, self.bottom
        return {
            "premium": {"lo": (1 - EDGE_ZONE) * t + EDGE_ZONE * b, "hi": t},
            "equilibrium": {"lo": EQ_ZONE[1] * b + EQ_ZONE[0] * t,
                            "hi": EQ_ZONE[1] * t + EQ_ZONE[0] * b},
            "discount": {"lo": b, "hi": (1 - EDGE_ZONE) * b + EDGE_ZONE * t},
        }


@dataclass
class _Pivot:
    current: float = math.nan
    last: float = math.nan
    crossed: bool = False
    bar: int = -1


def _settings(overrides: dict | None) -> dict:
    out = dict(LUXALGO_SETTINGS)
    for k, v in (overrides or {}).items():
        if k in out:
            out[k] = v
    return out


def _true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    tr = h - l
    if len(c) > 1:
        prev = c[:-1]
        tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - prev), np.abs(l[1:] - prev)])
    return tr


def _rma(src: np.ndarray, length: int) -> np.ndarray:
    """Pine ta.rma: SMA seed at bar length-1, then Wilder smoothing."""
    out = np.full(len(src), np.nan)
    if len(src) < length:
        return out
    out[length - 1] = src[:length].mean()
    alpha = 1.0 / length
    for i in range(length, len(src)):
        out[i] = alpha * src[i] + (1 - alpha) * out[i - 1]
    return out


def _gt(a: float, b: float) -> bool:
    """Pine comparison: anything against na is false."""
    return not (math.isnan(a) or math.isnan(b)) and a > b


def _lt(a: float, b: float) -> bool:
    return not (math.isnan(a) or math.isnan(b)) and a < b


def _prune(items: list, hit) -> list:
    """Remove items the way the indicator's `for [i, x] in array` + `remove(i)`
    loop does: the element shifted into a removed slot is skipped this bar."""
    out = list(items)
    k = 0
    while k < len(out):
        if hit(out[k]):
            out.pop(k)
        k += 1
    return out


def run_luxalgo(df: pd.DataFrame, settings: dict | None = None) -> LuxResult:
    """Replay the LuxAlgo SMC indicator over every bar of `df`."""
    cfg = _settings(settings)
    o = df["Open"].to_numpy(dtype=float)
    h = df["High"].to_numpy(dtype=float)
    l = df["Low"].to_numpy(dtype=float)
    c = df["Close"].to_numpy(dtype=float)
    n = len(c)

    tr = _true_range(h, l, c)
    atr = _rma(tr, ATR_LENGTH)
    if cfg["ob_filter"] == "range":
        idx = np.arange(n, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            vol = np.where(idx > 0, np.cumsum(tr) / idx, np.nan)
    else:
        vol = atr
    with np.errstate(invalid="ignore"):
        high_vol = (h - l) >= 2 * vol          # nan (warm-up) compares false
    parsed_hi = np.where(high_vol, l, h)
    parsed_lo = np.where(high_vol, h, l)

    lengths = {"swing": cfg["swing_length"], "internal": cfg["internal_length"], "eq": cfg["eq_length"]}
    legs = {k: 0 for k in lengths}
    prev_legs: dict[str, int | None] = {k: None for k in lengths}
    highs = {k: _Pivot() for k in lengths}
    lows = {k: _Pivot() for k in lengths}
    bias: dict[str, int] = {"swing": 0, "internal": 0}
    prev_lvl: dict[tuple[str, str], float] = {}

    top = bottom = math.nan
    top_bar = bottom_bar = range_bar = None
    structure: list[StructureEvent] = []
    equal_levels: list[EqualLevel] = []
    obs: dict[str, list[PriceZone]] = {"internal": [], "swing": []}
    fvgs: list[PriceZone] = []
    fvg_cum = 0.0
    drawn: dict = {"top": math.nan, "bottom": math.nan, "top_bar": None, "bottom_bar": None,
                   "range_bar": None, "swing_bias": 0}  # P/D zones + Strong/Weak as the chart last drew them

    for i in range(n):
        # trailing extremes (na until the first swing pivot seeds them)
        if not math.isnan(top):
            top = max(h[i], top)
            if top == h[i]:
                top_bar = i
        if not math.isnan(bottom):
            bottom = min(l[i], bottom)
            if bottom == l[i]:
                bottom_bar = i
        if i == n - 1:
            # the indicator draws these before this bar's pivots update
            drawn = {"top": top, "top_bar": top_bar, "bottom": bottom, "bottom_bar": bottom_bar,
                     "range_bar": range_bar, "swing_bias": bias["swing"]}

        # fair value gaps get mitigated before new ones are added
        if fvgs:
            fvgs = _prune(fvgs, lambda z: (z.side == "bull" and l[i] < z.y_lo)
                          or (z.side == "bear" and h[i] > z.y_hi))

        # legs and pivots: swing, internal, equal highs/lows
        for key, size in lengths.items():
            if i >= size:
                window_hi = h[i - size + 1: i + 1].max()
                window_lo = l[i - size + 1: i + 1].min()
                if h[i - size] > window_hi:
                    legs[key] = 0
                elif l[i - size] < window_lo:
                    legs[key] = 1
            prev = prev_legs[key]
            prev_legs[key] = legs[key]
            if prev is None or legs[key] == prev:
                continue
            p_bar = i - size
            if legs[key] == 1:          # start of bullish leg -> pivot low
                piv, level = lows[key], l[p_bar]
                if key == "eq" and _lt(abs(piv.current - level), cfg["eq_threshold"] * atr[i]):
                    equal_levels.append(EqualLevel("bull", piv.bar, piv.current, p_bar, level))
            else:                       # start of bearish leg -> pivot high
                piv, level = highs[key], h[p_bar]
                if key == "eq" and _lt(abs(piv.current - level), cfg["eq_threshold"] * atr[i]):
                    equal_levels.append(EqualLevel("bear", piv.bar, piv.current, p_bar, level))
            piv.last, piv.current, piv.crossed, piv.bar = piv.current, level, False, p_bar
            if key == "swing":
                range_bar = p_bar
                if legs[key] == 1:
                    bottom, bottom_bar = level, p_bar
                else:
                    top, top_bar = level, p_bar

        # BOS / CHoCH: internal first, then swing (indicator order)
        for scope in ("internal", "swing"):
            for side in ("bull", "bear"):
                piv = highs[scope] if side == "bull" else lows[scope]
                lvl = piv.current
                prev = prev_lvl.get((scope, side), math.nan)
                prev_lvl[(scope, side)] = lvl
                if i == 0:
                    continue
                if side == "bull":
                    crossed = _gt(c[i], lvl) and not math.isnan(prev) and c[i - 1] <= prev
                    extra = scope == "swing" or lvl != highs["swing"].current
                    if cfg["internal_confluence"] and scope == "internal":
                        extra = extra and (h[i] - max(c[i], o[i]) > min(c[i], o[i] - l[i]))
                else:
                    crossed = _lt(c[i], lvl) and not math.isnan(prev) and c[i - 1] >= prev
                    extra = scope == "swing" or lvl != lows["swing"].current
                    if cfg["internal_confluence"] and scope == "internal":
                        extra = extra and (h[i] - max(c[i], o[i]) < min(c[i], o[i] - l[i]))
                if not (crossed and not piv.crossed and extra):
                    continue
                against = -1 if side == "bull" else 1
                kind: LabelKind = "CHoCH" if bias[scope] == against else "BOS"
                piv.crossed = True
                bias[scope] = 1 if side == "bull" else -1
                structure.append(StructureEvent(kind, side, scope, piv.bar, i, lvl))
                if cfg["internal_obs" if scope == "internal" else "swing_obs"] and 0 <= piv.bar < i:
                    if side == "bear":
                        seg = parsed_hi[piv.bar:i]
                        j = piv.bar + int(np.argmax(seg))
                    else:
                        seg = parsed_lo[piv.bar:i]
                        j = piv.bar + int(np.argmin(seg))
                    ob = PriceZone("order_block", side, j, n - 1,
                                   float(parsed_lo[j]), float(parsed_hi[j]), scope)
                    obs[scope].insert(0, ob)
                    del obs[scope][MAX_STORED_OBS:]

        # order block mitigation
        bear_src = c[i] if cfg["ob_mitigation"] == "close" else h[i]
        bull_src = c[i] if cfg["ob_mitigation"] == "close" else l[i]
        for scope in ("internal", "swing"):
            if obs[scope]:
                obs[scope] = _prune(obs[scope], lambda z: (z.side == "bear" and bear_src > z.y_hi)
                                    or (z.side == "bull" and bull_src < z.y_lo))

        # new fair value gaps
        if i >= 1:
            delta = (c[i - 1] - o[i - 1]) / (o[i - 1] * 100) if o[i - 1] else 0.0
            fvg_cum += abs(delta)
            if i >= 2:
                thr = fvg_cum / i * 2 if cfg["fvg_auto_threshold"] else 0.0
                right = i + int(cfg["fvg_extend"])
                if l[i] > h[i - 2] and c[i - 1] > h[i - 2] and delta > thr:
                    fvgs.insert(0, PriceZone("fvg", "bull", i - 1, right, float(h[i - 2]), float(l[i])))
                if h[i] < l[i - 2] and c[i - 1] < l[i - 2] and -delta > thr:
                    fvgs.insert(0, PriceZone("fvg", "bear", i - 1, right, float(h[i]), float(l[i - 2])))

    # sweeps of each equal level after it formed (wick through, close back)
    swept: list[EqualLevel] = []
    for e in equal_levels:
        level = max(e.level0, e.level1) if e.side == "bear" else min(e.level0, e.level1)
        sweep = None
        for j in range(e.bar1 + 1, n):
            if e.side == "bear" and h[j] > level and c[j] < level:
                sweep = j
                break
            if e.side == "bull" and l[j] < level and c[j] > level:
                sweep = j
                break
        swept.append(replace(e, sweep_bar=sweep))

    shown_obs = obs["internal"][: int(cfg["internal_ob_count"])] if cfg["internal_obs"] else []
    if cfg["swing_obs"]:
        shown_obs = shown_obs + obs["swing"][: int(cfg["swing_ob_count"])]

    def side_of(b: int) -> Side | None:
        return "bull" if b == 1 else "bear" if b == -1 else None

    return LuxResult(
        n=n,
        structure=structure,
        equal_levels=swept,
        order_blocks=shown_obs,
        fvgs=fvgs,
        swing_bias=side_of(bias["swing"]),
        internal_bias=side_of(bias["internal"]),
        top=None if math.isnan(drawn["top"]) else float(drawn["top"]),
        top_bar=drawn["top_bar"],
        bottom=None if math.isnan(drawn["bottom"]) else float(drawn["bottom"]),
        bottom_bar=drawn["bottom_bar"],
        range_bar=drawn["range_bar"],
        range_bias=side_of(drawn["swing_bias"]),
        settings=cfg,
    )


# ──────────────────────────────────────────────────────── chart overlays

@dataclass(frozen=True)
class SmcOverlay:
    """LuxAlgo layers in window coordinates (bar 0 = first visible bar)."""
    zones: list[PriceZone]                 # order blocks + FVGs
    structure: list[StructureEvent]
    liquidity: list[EqualLevel]
    premium_discount: dict | None = None   # {"x0", "zones", "top", "bottom", labels...}


def default_smc_flags() -> dict:
    return {"zones": False, "structure": False, "liquidity": False}


def clean_smc_flags(raw) -> dict:
    incoming = raw if isinstance(raw, dict) else {}
    return {
        "zones": bool(incoming.get("zones", False)),
        "structure": bool(incoming.get("structure", False)),
        "liquidity": bool(incoming.get("liquidity", False)),
    }


def _shift_zone(z: PriceZone, vis_start: int, n: int) -> PriceZone | None:
    if max(z.x1, z.x0) < vis_start:
        return None
    return replace(z, x0=z.x0 - vis_start, x1=z.x1 - vis_start)


def detect_smc(
    df: pd.DataFrame,
    window: int = 30,
    flags: dict | None = None,
    premium_discount: bool = False,
    settings: dict | None = None,
) -> SmcOverlay:
    """Run LuxAlgo SMC on full history; return layers for the last `window` bars."""
    flags = clean_smc_flags(flags or {})
    if (not any(flags.values()) and not premium_discount) or df is None or len(df) < 5:
        return SmcOverlay([], [], [])

    work = df.reset_index(drop=True)
    res = run_luxalgo(work, settings)
    n = res.n
    vis_start = max(0, n - window)

    zones: list[PriceZone] = []
    if flags["zones"]:
        for z in res.order_blocks + res.fvgs:
            shifted = _shift_zone(z, vis_start, n)
            if shifted is not None:
                zones.append(shifted)

    structure: list[StructureEvent] = []
    if flags["structure"]:
        structure = [
            replace(e, pivot_bar=e.pivot_bar - vis_start, bar=e.bar - vis_start)
            for e in res.structure if e.bar >= vis_start
        ]

    liquidity: list[EqualLevel] = []
    if flags["liquidity"]:
        for e in res.equal_levels:
            if e.bar1 < vis_start:
                continue
            liquidity.append(replace(
                e,
                bar0=e.bar0 - vis_start,
                bar1=e.bar1 - vis_start,
                sweep_bar=e.sweep_bar - vis_start if e.sweep_bar is not None else None,
            ))

    pd_layer = None
    if premium_discount and res.zones() is not None:
        pd_layer = {
            "x0": (res.range_bar if res.range_bar is not None else 0) - vis_start,
            "zones": res.zones(),
            "top": res.top, "top_x": (res.top_bar or 0) - vis_start, "top_label": res.top_label,
            "bottom": res.bottom, "bottom_x": (res.bottom_bar or 0) - vis_start,
            "bottom_label": res.bottom_label,
        }

    return SmcOverlay(zones, structure, liquidity, pd_layer)


# ──────────────────────────────────────────────────────── analyst step 3

def _zone_dict(z: PriceZone) -> dict:
    out = {"kind": z.kind, "side": z.side, "lo": round(z.y_lo, 6), "hi": round(z.y_hi, 6), "bar": z.x0}
    if z.scope:
        out["scope"] = z.scope
    return out


def _nearest_below(zones: list[dict], price: float) -> dict | None:
    """Zone fully below price, closest to it from underneath."""
    under = [z for z in zones if z["hi"] < price]
    return max(under, key=lambda z: z["hi"]) if under else None


def _nearest_above(zones: list[dict], price: float) -> dict | None:
    """Zone fully above price, closest to it from overhead."""
    over = [z for z in zones if z["lo"] > price]
    return min(over, key=lambda z: z["lo"]) if over else None


def _event_dict(e: StructureEvent | None) -> dict | None:
    if e is None:
        return None
    return {"side": e.side, "scope": e.scope, "bar": e.bar, "price": round(float(e.price), 6)}


def _last(events: list[StructureEvent], kind: str | None = None, scope: str | None = None):
    for e in reversed(events):
        if (kind is None or e.kind == kind) and (scope is None or e.scope == scope):
            return e
    return None


def snapshot_smc(df: pd.DataFrame, settings: dict | None = None) -> dict:
    """Absolute-price LuxAlgo SMC read for the analyst: dealing range and
    Premium/Equilibrium/Discount, Strong/Weak High/Low, swing and internal
    BOS/CHoCH, the chart's active order blocks, EQH/EQL and open FVGs."""
    cfg = _settings(settings)
    min_bars = 2 * int(cfg["internal_length"]) + 1
    if df is None or len(df) < min_bars:
        return {"ok": False, "error": "not enough bars"}

    work = df.reset_index(drop=True)
    res = run_luxalgo(work, cfg)
    price = float(work["Close"].iloc[-1])
    out: dict = {"ok": True, "price": round(price, 6)}

    zones = res.zones()
    if zones is not None and res.top is not None and res.bottom is not None:
        lo, hi = res.bottom, res.top
        span = hi - lo
        pos_pct = round((price - lo) / span * 100.0, 2) if span > 0 else 50.0
        out["range"] = {
            "lo": round(lo, 6), "hi": round(hi, 6), "mid": round((lo + hi) / 2.0, 6),
            "lo_bar": res.bottom_bar, "hi_bar": res.top_bar,
        }
        out["zones"] = {k: {"lo": round(v["lo"], 6), "hi": round(v["hi"], 6)} for k, v in zones.items()}
        out["price_position_pct"] = pos_pct
        out["price_zone"] = "premium" if pos_pct > 50 else "discount"
        eq = zones["equilibrium"]
        out["near_equilibrium"] = eq["lo"] <= price <= eq["hi"]
        out["in_zone"] = next(
            (k for k, v in zones.items() if v["lo"] <= price <= v["hi"]), None
        )
        out["strong_weak"] = {
            "high": {"label": res.top_label, "level": round(hi, 6), "bar": res.top_bar},
            "low": {"label": res.bottom_label, "level": round(lo, 6), "bar": res.bottom_bar},
        }
    else:
        out["range"] = None
        out["zones"] = None
        out["price_zone"] = "n/a"
        out["price_position_pct"] = None
        out["near_equilibrium"] = None
        out["in_zone"] = None
        out["strong_weak"] = None

    out["swing_bias"] = res.swing_bias
    out["internal_bias"] = res.internal_bias
    out["last_choch"] = _event_dict(_last(res.structure, "CHoCH"))
    out["last_bos"] = _event_dict(_last(res.structure, "BOS"))
    out["swing_structure"] = {
        "last_bos": _event_dict(_last(res.structure, "BOS", "swing")),
        "last_choch": _event_dict(_last(res.structure, "CHoCH", "swing")),
    }
    out["internal_structure"] = {
        "last_bos": _event_dict(_last(res.structure, "BOS", "internal")),
        "last_choch": _event_dict(_last(res.structure, "CHoCH", "internal")),
    }

    active_obs = [_zone_dict(z) for z in res.order_blocks]
    out["order_blocks"] = active_obs
    out["demand_below"] = _nearest_below([z for z in active_obs if z["side"] == "bull"], price)
    out["supply_above"] = _nearest_above([z for z in active_obs if z["side"] == "bear"], price)
    if out["supply_above"] is not None:
        out["wall"] = {
            "price": out["supply_above"]["lo"],
            "distance_pct": round((out["supply_above"]["lo"] - price) / price * 100.0, 2),
        }
    else:
        out["wall"] = None

    def eq_map(side: Side) -> dict | None:
        marks = [e for e in res.equal_levels if e.side == side]
        if not marks:
            return None
        e = marks[-1]
        level = max(e.level0, e.level1) if side == "bear" else min(e.level0, e.level1)
        return {"level": round(level, 6), "pool_bar": e.bar1, "sweep_bar": e.sweep_bar}

    out["eqh"] = eq_map("bear")
    out["eql"] = eq_map("bull")
    out["open_fvgs"] = [_zone_dict(z) for z in res.fvgs[:4]]
    return out
