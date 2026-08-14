"""Backtest the SMA crossover strategy on historical data (yfinance).

Run:  python backtest.py
Saves raw data, trades and the equity curve to output/.
"""
import sys

import pandas as pd

import config
from engine import compute_metrics, run_backtest


def download_data() -> pd.DataFrame:
    import yfinance as yf

    df = yf.Ticker(config.SYMBOL).history(
        start=config.BACKTEST_START, auto_adjust=True, actions=False
    )
    if df.empty:
        sys.exit("No data downloaded - check your internet connection.")
    return df


def main() -> None:
    df = download_data()
    curve, trades = run_backtest(
        df,
        strategy="sma_crossover",
        params={"fast": config.SMA_FAST, "slow": config.SMA_SLOW},
        qty=config.QUANTITY,
        capital=config.CAPITAL,
    )

    trades.to_csv(config.OUTPUT_DIR / "trades.csv", index=False)
    curve.to_csv(config.OUTPUT_DIR / "equity_curve.csv")
    df[["Open", "High", "Low", "Close", "Volume"]].to_csv(
        config.OUTPUT_DIR / f"data_{config.SYMBOL}.csv"
    )

    m = compute_metrics(curve, trades, config.CAPITAL)
    print(f"\n=== Backtest: {config.SYMBOL} | SMA {config.SMA_FAST}/{config.SMA_SLOW} | qty {config.QUANTITY} ===")
    print(f"  Period            : {m['start']} -> {m['end']}")
    print(f"  Starting capital  : ${config.CAPITAL:,.0f}")
    print(f"  Final equity      : ${m['final_equity']:,.2f}")
    print(f"  Total return      : {m['total_return']:.2%}   (buy & hold: {m['buy_hold']:.2%})")
    print(f"  CAGR              : {m['cagr']:.2%}")
    print(f"  Max drawdown      : {m['max_drawdown']:.2%}")
    if m["trades"]:
        print(f"  Trades            : {m['trades']}  (wins: {m['wins']}, win rate: {m['wins'] / m['trades']:.1%})")
    else:
        print("  Trades            : 0")
    print(f"  Outputs           : {config.OUTPUT_DIR / 'trades.csv'}")
    print(f"                      {config.OUTPUT_DIR / 'equity_curve.csv'}")
    print(f"                      {config.OUTPUT_DIR / f'data_{config.SYMBOL}.csv'}")


if __name__ == "__main__":
    main()