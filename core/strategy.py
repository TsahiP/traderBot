import pandas as pd


def compute_signals(df: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """Add SMA columns and a signal column to a DataFrame of price bars.

    Signal: +1 when fast SMA crosses above slow SMA (buy), -1 when it
    crosses below (sell), 0 otherwise. Requires columns: 'Close'.
    """
    df = df.copy()
    df["sma_fast"] = df["Close"].rolling(fast).mean()
    df["sma_slow"] = df["Close"].rolling(slow).mean()

    df["signal"] = 0
    now_above = (df["sma_fast"] > df["sma_slow"]).fillna(False).astype(bool)
    prev_above = now_above.shift(1).fillna(False).astype(bool)
    df.loc[now_above & ~prev_above, "signal"] = 1
    df.loc[~now_above & prev_above, "signal"] = -1

    return df


def latest_signal(df: pd.DataFrame) -> int:
    """Return the most recent non-zero signal in the DataFrame."""
    nonzero = df.loc[df["signal"] != 0]
    if nonzero.empty:
        return 0
    return int(nonzero["signal"].iloc[-1])