"""Render recent candles as a dark-themed PNG for Telegram alerts."""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from smc import SmcOverlay, detect_smc

UP = "#26a69a"
DOWN = "#ef5350"
PROFIT = "#22c55e"
BG = "#0b1220"
GRID = "#1e293b"
TEXT = "#cbd5e1"
HIGHLIGHT = "#f59e0b"
RR_ALPHA = 0.25
# LuxAlgo SMC palette (Colored style)
LUX_BULL = "#089981"
LUX_BEAR = "#F23645"
LUX_EQ = "#878b94"
LUX_EQ_TEXT = "#b2b5be"
OB_BULL = "#3179f5"
OB_BEAR = "#f77c80"
FVG_BULL = "#00ff68"
FVG_BEAR = "#ff0008"
LUX_ZONE_ALPHA = 0.2
FVG_ALPHA = 0.3


def _tick_labels(index, n: int) -> list[str]:
    """~6 date labels; intraday bars get HH:MM too."""
    step = max(1, (n - 1) // 5) if n > 1 else 1
    out = []
    for i in range(0, n, step):
        ts = index[i]
        out.append(ts.strftime("%m-%d %H:%M") if hasattr(ts, "hour") and (ts.hour or ts.minute) else ts.strftime("%m-%d"))
    return out


def _draw_rr_overlay(ax, x0: float, x1: float, direction: str, levels: dict) -> None:
    """TradingView-style risk/reward boxes from the signal bar toward the right."""
    entry = float(levels["entry"])
    stop = float(levels["stop"])
    target = levels.get("target")
    width = x1 - x0

    if direction == "long":
        risk_lo, risk_hi = stop, entry
        profit = (entry, float(target)) if target is not None else None
    else:
        risk_lo, risk_hi = entry, stop
        profit = (float(target), entry) if target is not None else None

    ax.add_patch(
        Rectangle(
            (x0, risk_lo),
            width,
            risk_hi - risk_lo,
            facecolor=DOWN,
            alpha=RR_ALPHA,
            edgecolor="none",
            zorder=1.2,
        )
    )
    if profit is not None:
        p_lo, p_hi = profit
        ax.add_patch(
            Rectangle(
                (x0, p_lo),
                width,
                p_hi - p_lo,
                facecolor=PROFIT,
                alpha=RR_ALPHA,
                edgecolor="none",
                zorder=1.2,
            )
        )

    ax.axhline(entry, color=HIGHLIGHT, linestyle=":", linewidth=1.2, zorder=4)


def _draw_smc(ax, overlay: SmcOverlay, n: int, x_right: float | None = None) -> None:
    """LuxAlgo SMC look: OB / FVG boxes, BOS-CHoCH lines, EQH/EQL, P/E/D zones."""
    x_right = n - 0.5 if x_right is None else x_right
    pd_layer = overlay.premium_discount
    if pd_layer:
        x0 = max(-0.5, pd_layer["x0"])
        last = n - 0.5
        for name, color in (("premium", LUX_BEAR), ("equilibrium", LUX_EQ), ("discount", LUX_BULL)):
            z = pd_layer["zones"][name]
            ax.add_patch(Rectangle((x0, z["lo"]), last - x0, z["hi"] - z["lo"], facecolor=color,
                                   alpha=LUX_ZONE_ALPHA, edgecolor="none", zorder=1.0))
        mid = (x0 + last) / 2
        prem, eq, disc = (pd_layer["zones"][k] for k in ("premium", "equilibrium", "discount"))
        ax.annotate("Premium", xy=(mid, prem["hi"]), xytext=(0, 2), textcoords="offset points",
                    ha="center", va="bottom", color=LUX_BEAR, fontsize=7, zorder=6)
        ax.annotate("Equilibrium", xy=(last, (eq["lo"] + eq["hi"]) / 2), xytext=(4, 0),
                    textcoords="offset points", ha="left", va="center", color=LUX_EQ_TEXT,
                    fontsize=7, zorder=6)
        ax.annotate("Discount", xy=(mid, disc["lo"]), xytext=(0, -2), textcoords="offset points",
                    ha="center", va="top", color=LUX_BULL, fontsize=7, zorder=6)
        for key, color, va in (("top", LUX_BEAR, "bottom"), ("bottom", LUX_BULL, "top")):
            level = pd_layer[key]
            ax.plot([max(-0.5, pd_layer[f"{key}_x"]), x_right], [level, level],
                    color=color, linewidth=0.9, zorder=4)
            ax.annotate(pd_layer[f"{key}_label"], xy=(x_right, level),
                        xytext=(0, 2 if va == "bottom" else -2), textcoords="offset points",
                        ha="right", va=va, color=color, fontsize=6.5, zorder=6)

    for z in overlay.zones:
        x0 = max(-0.5, z.x0 - 0.5)
        x1 = x_right if z.kind == "order_block" else min(x_right, z.x1 + 0.5)
        if x1 <= x0:
            continue
        lo, hi = min(z.y_lo, z.y_hi), max(z.y_lo, z.y_hi)
        if z.kind == "order_block":
            color = OB_BULL if z.side == "bull" else OB_BEAR
            ax.add_patch(Rectangle((x0, lo), x1 - x0, hi - lo, facecolor=color,
                                   alpha=LUX_ZONE_ALPHA, edgecolor="none", zorder=1.1))
        else:
            color = FVG_BULL if z.side == "bull" else FVG_BEAR
            mid = (lo + hi) / 2
            for b_lo, b_hi in ((lo, mid), (mid, hi)):   # LuxAlgo draws two halves
                ax.add_patch(Rectangle((x0, b_lo), x1 - x0, b_hi - b_lo, facecolor=color,
                                       alpha=FVG_ALPHA, edgecolor=color, linewidth=0.4,
                                       zorder=1.15))

    for m in overlay.structure:
        if m.bar < 0 or m.bar >= n:
            continue
        color = LUX_BULL if m.side == "bull" else LUX_BEAR
        swing = m.scope == "swing"
        x0 = max(-0.5, m.pivot_bar)
        ax.plot([x0, m.bar], [m.price, m.price], color=color, linewidth=0.9 if swing else 0.7,
                linestyle="-" if swing else (0, (3, 2)), zorder=4)
        ax.annotate(m.kind, xy=(round((m.pivot_bar + m.bar) / 2), m.price),
                    xytext=(0, 1.5 if m.side == "bull" else -1.5), textcoords="offset points",
                    ha="center", va="bottom" if m.side == "bull" else "top", color=color,
                    fontsize=7.5 if swing else 6, zorder=5)

    for e in overlay.liquidity:
        if e.bar1 < 0:
            continue
        color = LUX_BEAR if e.side == "bear" else LUX_BULL
        ax.plot([max(-0.5, e.bar0), e.bar1], [e.level0, e.level1], color=color,
                linewidth=0.8, linestyle=":", zorder=4)
        ax.annotate("EQH" if e.side == "bear" else "EQL",
                    xy=(round((e.bar0 + e.bar1) / 2), (e.level0 + e.level1) / 2),
                    xytext=(0, 1.5 if e.side == "bear" else -1.5), textcoords="offset points",
                    ha="center", va="bottom" if e.side == "bear" else "top", color=color,
                    fontsize=6, zorder=5)


def render_candles(
    df,
    symbol: str,
    timeframe: str,
    pattern_label: str,
    direction: str,
    levels: dict | None = None,
    smc_flags: dict | None = None,
) -> bytes:
    """Last up-to-30 bars as a PNG; the signal bar is highlighted in amber."""
    window = 30
    d = df.tail(window).reset_index()
    n = len(d)
    if n == 0:
        raise ValueError("no bars to render")

    overlay = detect_smc(df, window=window, flags=smc_flags)

    fig, ax = plt.subplots(figsize=(8.4, 4.6), dpi=130)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    rr_x0 = n - 1.0
    rr_x1 = n + 1.5
    if overlay.zones or overlay.structure or overlay.liquidity:
        _draw_smc(ax, overlay, n, x_right=rr_x1 if levels is not None else n - 0.2)
    if levels is not None:
        _draw_rr_overlay(ax, rr_x0, rr_x1, direction, levels)

    for i in range(n):
        o, h, l, c = (float(d["Open"].iloc[i]), float(d["High"].iloc[i]),
                      float(d["Low"].iloc[i]), float(d["Close"].iloc[i]))
        color = UP if c >= o else DOWN
        ax.vlines(i, l, h, color=color, linewidth=1.0, zorder=2)
        body_lo = min(o, c)
        body_h = max(abs(c - o), (h - l) * 0.03)
        ax.add_patch(Rectangle((i - 0.35, body_lo), 0.7, body_h,
                               facecolor=color, edgecolor="none", zorder=3))

    # highlight the bar that completed the pattern
    ax.axvspan(n - 1.6, n + 0.4, color=HIGHLIGHT, alpha=0.15, zorder=1)
    top = float(d["High"].iloc[-1])
    bottom = float(d["Low"].iloc[-1])
    span = max(top - bottom, 1e-9)
    ax.annotate(
        pattern_label,
        xy=(n - 1, top),
        xytext=(-4, 16),
        textcoords="offset points",
        ha="right",
        color=HIGHLIGHT,
        fontsize=10,
        fontweight="bold",
    )

    arrow = "LONG" if direction == "long" else "SHORT"
    ax.set_title(f"{symbol} · {timeframe} — {pattern_label} ({arrow})",
                 color=TEXT, fontsize=12, loc="left", pad=10)
    x_right = rr_x1 + 0.3 if levels is not None else n - 0.2
    ax.set_xlim(-0.8, x_right)
    lo = float(d["Low"].min())
    hi = float(d["High"].max())
    prices = [lo, hi]   # SMC boxes beyond the candles get clipped, not zoomed out to
    if levels is not None:
        prices.extend([float(levels["entry"]), float(levels["stop"])])
        if levels.get("target") is not None:
            prices.append(float(levels["target"]))
        lo_p = min(prices)
        hi_p = max(prices)
        span = max(hi_p - lo_p, 1e-9)
        ax.set_ylim(lo_p - span * 0.10, hi_p + span * 0.12)
    else:
        lo_p = min(prices)
        hi_p = max(prices)
        span = max(hi_p - lo_p, 1e-9)
        ax.set_ylim(lo_p - span * 0.08, hi_p + span * 0.14)

    labels = _tick_labels(df.tail(n).index, n)
    tick_pos = list(range(0, n, max(1, (n - 1) // 5))) if n > 1 else [0]
    ax.set_xticks(tick_pos)
    ax.set_xticklabels(labels[: len(tick_pos)], color=TEXT, fontsize=8)

    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.tick_params(colors=TEXT, length=3)
    ax.grid(axis="y", color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
