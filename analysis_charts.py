"""Analyst chart PNGs: candles + SMC overlay + Premium/EQ/Discount lines +
Squeeze Momentum subpanel, in the same dark style as chart_image.py.
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from chart_image import (
    BG,
    DOWN,
    GRID,
    TEXT,
    UP,
    _draw_smc,
    _tick_labels,
)
from smc import detect_smc, snapshot_smc
import squeeze as sq

ANALYSIS_WINDOW = 120
MIN_BARS = 30
FULL_SMC = {"zones": True, "structure": True, "liquidity": True}

SQUEEZE_COLORS = {
    sq.BRIGHT_GREEN: "#00e676",
    sq.DARK_GREEN: "#15803d",
    sq.DARK_RED: "#b91c1c",
    sq.PINK: "#ff4d6d",
}

PREMIUM_LINE = "#ef5350"
EQ_LINE = "#f59e0b"
DISCOUNT_LINE = "#26a69a"


def _candles(ax, d) -> None:
    for i in range(len(d)):
        o, h, l, c = (
            float(d["Open"].iloc[i]), float(d["High"].iloc[i]),
            float(d["Low"].iloc[i]), float(d["Close"].iloc[i]),
        )
        color = UP if c >= o else DOWN
        ax.vlines(i, l, h, color=color, linewidth=0.8, zorder=2)
        body_lo, body_h = min(o, c), max(abs(c - o), (h - l) * 0.03)
        ax.add_patch(Rectangle((i - 0.32, body_lo), 0.64, body_h,
                               facecolor=color, edgecolor="none", zorder=3))


def _range_lines(ax, d, radius: int) -> None:
    """Premium / Equilibrium / Discount guides from this timeframe's snapshot."""
    lows = d["Low"].astype(float)
    highs = d["High"].astype(float)
    lo_p, hi_p = float(lows.min()), float(highs.max())
    span = max(hi_p - lo_p, 1e-9)
    ax.set_ylim(lo_p - span * 0.08, hi_p + span * 0.14)

    smc = snapshot_smc(d, radius=radius)
    rng = smc.get("range") if smc.get("ok") else None
    if not rng:
        return
    guides = [
        (float(rng["hi"]), PREMIUM_LINE, "Premium", (5, 0)),
        (float(rng["mid"]), EQ_LINE, "EQ 50%", (0, ())),
        (float(rng["lo"]), DISCOUNT_LINE, "Discount", (5, 0)),
    ]
    for level, color, label, dash in guides:
        y_lo, y_hi = ax.get_ylim()
        if y_lo <= level <= y_hi:
            ax.axhline(level, color=color, linewidth=0.9, linestyle="--", zorder=1.2)
            ax.annotate(
                f"{label} {level:g}",
                xy=(1.0, level),
                xycoords=("axes fraction", "data"),
                xytext=(2, -3),
                textcoords="offset points",
                ha="left", va="top",
                color=color, fontsize=7.5, zorder=6,
            )


def render_analysis_chart(df, symbol: str, timeframe: str, radius: int = 2) -> bytes:
    """Last ~120 bars: candles, SMC layers, P/E/D guides and a squeeze panel."""
    if df is None or len(df) < MIN_BARS or df["Close"].isna().any():
        raise ValueError("not enough bars to render")

    window = min(ANALYSIS_WINDOW, len(df))
    d = df.tail(window).reset_index(drop=True)
    n = len(d)
    overlay = detect_smc(df, window=window, flags=FULL_SMC)

    vals = sq.momentum_series(df).tail(n).reset_index(drop=True)
    colors = [
        SQUEEZE_COLORS[sq.bar_color(float(v), float(vals.iloc[i - 1]) if i else None)]
        if v == v else GRID
        for i, v in enumerate(vals)
    ]

    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(9.6, 6.2), dpi=130,
        gridspec_kw={"height_ratios": (3.0, 1.0), "hspace": 0.06},
        sharex=True,
    )
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax2.set_facecolor(BG)

    if overlay.zones or overlay.structure or overlay.liquidity:
        _draw_smc(ax, overlay, n)
    _candles(ax, d)
    _range_lines(ax, d, radius=radius if radius >= 0 else 2)

    ax2.bar(range(n), [0.0 if v != v else float(v) for v in vals],
            color=colors, width=0.85, linewidth=0, zorder=2)
    ax2.axhline(0, color=GRID, linewidth=0.8, zorder=1)
    sym = "+" if vals.iloc[-1] == vals.iloc[-1] and float(vals.iloc[-1]) > 0 else "-"
    ax2.set_title(f"Squeeze Momentum {sym}{abs(float(vals.iloc[-1])):.2f}",
                  color=TEXT, fontsize=8, loc="left", pad=2)

    last_close = float(d["Close"].iloc[-1])
    prev_close = float(d["Close"].iloc[-2])
    drift = "▲" if last_close >= prev_close else "▼"
    ax.set_title(
        f"{symbol} · {timeframe} — SMC + Squeeze   {drift} {last_close:g}",
        color=TEXT, fontsize=12, loc="left", pad=10,
    )
    ax.set_xlim(-0.8, n - 0.2)
    labels = _tick_labels(df.tail(n).index, n)
    tick_pos = list(range(0, n, max(1, (n - 1) // 5))) if n > 1 else [0]
    ax2.set_xticks(tick_pos)
    ax2.set_xticklabels(labels[: len(tick_pos)], color=TEXT, fontsize=8)

    for axes in (ax, ax2):
        for spine in axes.spines.values():
            spine.set_color(GRID)
        axes.tick_params(colors=TEXT, length=3)
        axes.grid(axis="y", color=GRID, linewidth=0.5)
        axes.set_axisbelow(True)
    ax2.margins(y=0.4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
