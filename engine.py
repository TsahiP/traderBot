"""Shared backtest engine: SMA crossover simulation + metrics.

Used by both the CLI (backtest.py) and the dashboard API (dashboard.py)
so every run - script or UI - applies identical logic.
"""
import pandas as pd

from strategy import compute_signals


def run_backtest(
    df: pd.DataFrame, fast: int, slow: int, qty: int, capital: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate fixed-quantity SMA crossover trading, executing at the
    next bar's open. Returns (curve, trades_df)."""
    df = compute_signals(df, fast, slow)
    df["order"] = df["signal"].shift(1)  # act on the open of the next bar

    cash = float(capital)
    shares = 0
    entry_price = None
    entry_date = None
    trades = []

    for idx, row in df.iterrows():
        if row["order"] == 1 and shares == 0 and cash >= row["Open"] * qty:
            shares = qty
            cash -= row["Open"] * qty
            entry_price = row["Open"]
            entry_date = idx
        elif row["order"] == -1 and shares > 0:
            cash += row["Open"] * shares
            trades.append(
                {
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "exit_date": idx,
                    "exit_price": row["Open"],
                    "pnl": (row["Open"] - entry_price) * shares,
                }
            )
            shares = 0
            entry_price = None

        df.at[idx, "shares"] = shares
        df.at[idx, "cash"] = cash
        df.at[idx, "equity"] = cash + shares * row["Close"]

    trades_df = pd.DataFrame(trades)
    curve = df[
        ["Open", "High", "Low", "Close", "Volume", "sma_fast", "sma_slow",
         "signal", "order", "shares", "cash", "equity"]
    ].copy()
    curve.index.name = "date"
    return curve, trades_df


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def compute_metrics(curve: pd.DataFrame, trades_df: pd.DataFrame, capital: float) -> dict:
    equity = curve["equity"]
    final_equity = float(equity.iloc[-1])
    days = (equity.index[-1] - equity.index[0]).days
    years = max(days / 365.25, 1e-9)
    n_trades = len(trades_df)
    wins = int((trades_df["pnl"] > 0).sum()) if n_trades else 0
    return {
        "start": str(curve.index[0].date()),
        "end": str(curve.index[-1].date()),
        "final_equity": round(final_equity, 2),
        "total_return": final_equity / capital - 1,
        "cagr": (final_equity / capital) ** (1 / years) - 1,
        "max_drawdown": max_drawdown(equity),
        "buy_hold": float(curve["Close"].iloc[-1] / curve["Close"].iloc[0] - 1),
        "trades": n_trades,
        "wins": wins,
        "win_rate": wins / n_trades if n_trades else None,
    }