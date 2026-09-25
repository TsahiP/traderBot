"""Render recent candles as a dark-themed PNG for Telegram alerts."""
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

UP = "#26a69a"
DOWN = "#ef5350"
BG = "#0b1220"
GRID = "#1e293b"
TEXT = "#cbd5e1"
HIGHLIGHT = "#f59e0b"


def _tick_labels(index, n: int) -> list[str]:
    """~6 date labels; intraday bars get HH:MM too."""
    step = max(1, (n - 1) // 5) if n > 1 else 1
    out = []
    for i in range(0, n, step):
        ts = index[i]
        out.append(ts.strftime("%m-%d %H:%M") if hasattr(ts, "hour") and (ts.hour or ts.minute) else ts.strftime("%m-%d"))
    return out


def render_candles(df, symbol: str, timeframe: str, pattern_label: str, direction: str) -> bytes:
    """Last up-to-30 bars as a PNG; the signal bar is highlighted in amber."""
    d = df.tail(30).reset_index()
    n = len(d)
    if n == 0:
        raise ValueError("no bars to render")

    fig, ax = plt.subplots(figsize=(8.4, 4.6), dpi=130)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

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
    ax.set_xlim(-0.8, n - 0.2)
    lo = float(d["Low"].min())
    hi = float(d["High"].max())
    ax.set_ylim(lo - span * 0.08, hi + span * 0.14)

    idx = d.index
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
