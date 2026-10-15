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
SMC_ZONE_ALPHA = 0.18
LIQUIDITY_LINE = "#64748b"


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


def _draw_smc(ax, overlay: SmcOverlay, n: int) -> None:
    for z in overlay.zones:
        color = UP if z.side == "bull" else DOWN
        x0 = max(0, z.x0)
        x1 = min(n - 1, max(z.x1, x0))
        width = max(x1 - x0 + 0.9, 0.4)
        ax.add_patch(
            Rectangle(
                (x0, z.y_lo),
                width,
                z.y_hi - z.y_lo,
                facecolor=color,
                alpha=SMC_ZONE_ALPHA,
                edgecolor=color,
                linewidth=0.6,
                linestyle="--" if z.kind == "fvg" else "-",
                zorder=1.1,
            )
        )

    for m in overlay.structure:
        if m.bar < 0 or m.bar >= n:
            continue
        label = m.kind
        ax.annotate(
            label,
            xy=(m.bar, m.price),
            xytext=(0, 10 if m.side == "bull" else -12),
            textcoords="offset points",
            ha="center",
            color=HIGHLIGHT,
            fontsize=8,
            fontweight="bold",
            zorder=5,
        )

    for liq in overlay.liquidity:
        ax.axhline(liq.level, color=LIQUIDITY_LINE, linewidth=0.9, linestyle="-.", zorder=1.15)
        if liq.sweep_bar is not None and 0 <= liq.sweep_bar < n:
            ax.scatter(
                [liq.sweep_bar],
                [liq.level],
                marker="x",
                color=LIQUIDITY_LINE,
                s=36,
                linewidths=1.2,
                zorder=5,
            )


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

    if overlay.zones or overlay.structure or overlay.liquidity:
        _draw_smc(ax, overlay, n)

    rr_x0 = n - 1.0
    rr_x1 = n + 1.5
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
    prices = [lo, hi]
    for z in overlay.zones:
        prices.extend([z.y_lo, z.y_hi])
    for liq in overlay.liquidity:
        prices.append(liq.level)
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
