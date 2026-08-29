import numpy as np
import pandas as pd
import pytest

import engine


def make_sample_bars(prices: list[float], dates: list[str] | None = None) -> pd.DataFrame:
    n = len(prices)
    if dates is None:
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
    else:
        idx = pd.to_datetime(dates)
    df = pd.DataFrame(
        {
            "Open": prices,
            "High": [p * 1.01 for p in prices],
            "Low": [p * 0.99 for p in prices],
            "Close": prices,
            "Volume": [1000] * n,
        },
        index=idx,
    )
    df.index.name = "date"
    return df


def test_linear_cost_calculation(monkeypatch):
    """Verify cost is charged linearly per share: 2 * cost_per_share * qty per round trip."""
    # 5 bars: buy on bar 2 open, sell on bar 4 open
    # Bar 0: price 100
    # Bar 1: price 105 (signal +1 emitted)
    # Bar 2: price 110 (order +1 executed at Open=110)
    # Bar 3: price 115 (signal -1 emitted)
    # Bar 4: price 120 (order -1 executed at Open=120)
    df = make_sample_bars([100.0, 105.0, 110.0, 115.0, 120.0])
    qty = 50
    cost_per_share = 0.05
    capital = 10_000.0

    def mock_strategy(strategy_id, df, params, allow_short=False):
        df["signal"] = [0, 1, 0, -1, 0]
        return df

    monkeypatch.setattr(engine, "run_strategy", mock_strategy)

    curve, trades = engine.run_backtest(
        df,
        strategy="mock",
        qty=qty,
        capital=capital,
        cost_per_share=cost_per_share,
    )

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["entry_price"] == 110.0
    assert trade["exit_price"] == 120.0
    assert trade["side"] == "long"
    assert trade["exit_type"] == "signal"

    expected_cost = 2 * (cost_per_share * qty)  # 2 * 0.05 * 50 = 5.00
    assert trade["costs"] == expected_cost

    # Gross gain = (120 - 110) * 50 = 500. Net PnL = 500 - 5.00 = 495.00
    expected_pnl = 495.0
    assert trade["pnl"] == expected_pnl

    # Cash at end should be capital + pnl
    assert round(curve["cash"].iloc[-1], 2) == round(capital + expected_pnl, 2)
    assert round(curve["equity"].iloc[-1], 2) == round(capital + expected_pnl, 2)


def test_short_trading_execution_and_costs(monkeypatch):
    """Verify short selling entry, exit, and costs."""
    df = make_sample_bars([100.0, 100.0, 100.0, 80.0, 80.0])
    qty = 10
    cost_per_share = 0.10
    capital = 5_000.0

    def mock_short_strategy(strategy_id, df, params, allow_short=False):
        df["signal"] = [0, -1, 0, 1, 0]
        return df

    monkeypatch.setattr(engine, "run_strategy", mock_short_strategy)

    curve, trades = engine.run_backtest(
        df,
        strategy="mock_short",
        qty=qty,
        capital=capital,
        allow_short=True,
        cost_per_share=cost_per_share,
    )

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["side"] == "short"
    assert trade["entry_price"] == 100.0
    assert trade["exit_price"] == 80.0

    # Short profit: (100 - 80) * 10 = 200. Costs: 2 * 0.10 * 10 = 2.00. Net PnL = 198.00
    assert trade["costs"] == 2.0
    assert trade["pnl"] == 198.0
    assert round(curve["equity"].iloc[-1], 2) == round(capital + 198.0, 2)


def test_flat_eod_forces_close_at_session_close(monkeypatch):
    """Verify flat_eod force closes open position on the last bar of intraday session."""
    # 2 days of 3 bars each
    dates = [
        "2026-01-01 09:30", "2026-01-01 12:00", "2026-01-01 15:55",
        "2026-01-02 09:30", "2026-01-02 12:00", "2026-01-02 15:55",
    ]
    df = make_sample_bars([100.0, 102.0, 105.0, 105.0, 106.0, 108.0], dates=dates)
    qty = 10
    capital = 10_000.0

    def mock_eod_strat(strategy_id, df, params, allow_short=False):
        # Signal buy on bar 0 (executes bar 1 open), no exit signal emitted
        df["signal"] = [1, 0, 0, 0, 0, 0]
        return df

    monkeypatch.setattr(engine, "run_strategy", mock_eod_strat)

    curve, trades = engine.run_backtest(
        df,
        strategy="mock_eod",
        qty=qty,
        capital=capital,
        flat_eod=True,
    )

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["exit_type"] == "eod"
    assert trade["entry_price"] == 102.0
    # Exited at bar 2 Close = 105.0
    assert trade["exit_price"] == 105.0
    assert trade["pnl"] == (105.0 - 102.0) * qty
    assert curve["shares"].iloc[2] == 0  # flattened at bar 2


def test_open_position_at_end_of_data(monkeypatch):
    """Verify unclosed trade at end of dataset is captured with exit_type='open'."""
    df = make_sample_bars([100.0, 102.0, 108.0])
    qty = 10
    capital = 10_000.0

    def mock_open_strat(strategy_id, df, params, allow_short=False):
        df["signal"] = [1, 0, 0]  # buys on bar 1 open=102.0, holds through end
        return df

    monkeypatch.setattr(engine, "run_strategy", mock_open_strat)

    curve, trades = engine.run_backtest(
        df,
        strategy="mock_open",
        qty=qty,
        capital=capital,
        cost_per_share=0.05,
    )

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["exit_type"] == "open"
    assert trade["exit_date"] is None
    assert trade["exit_price"] is None
    # Unrealized PnL at close of bar 2 (108.0): (108 - 102) * 10 - (0.05 * 10) = 60 - 0.5 = 59.50
    assert trade["pnl"] == 59.5


def test_compute_metrics_resilience():
    """Verify compute_metrics handles edge cases like empty data, bankruptcy, and zero trades."""
    empty_df = pd.DataFrame()
    metrics = engine.compute_metrics(empty_df, pd.DataFrame(), 100_000.0)
    assert metrics["trades"] == 0
    assert metrics["cagr"] == 0.0
    assert metrics["buy_hold"] == 0.0
    assert metrics["total_return"] == 0.0
    assert not np.isnan(metrics["buy_hold"])

    # Test bankruptcy (equity <= 0)
    df = make_sample_bars([100.0, 50.0, 10.0])
    df["equity"] = [100.0, 0.0, -100.0]
    metrics_bankrupt = engine.compute_metrics(df, pd.DataFrame(), 10_000.0)
    assert metrics_bankrupt["cagr"] == -1.0
    assert metrics_bankrupt["total_return"] < -1.0
    assert not np.isnan(metrics_bankrupt["cagr"])


def test_compute_metrics_nan_and_inf_safety():
    """Verify compute_metrics never produces NaN or Inf even with corrupted price/equity data."""
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    df = pd.DataFrame(
        {
            "Open": [100.0, 105.0, np.nan, 110.0, np.nan],
            "High": [101.0, 106.0, np.nan, 111.0, np.nan],
            "Low": [99.0, 104.0, np.nan, 109.0, np.nan],
            "Close": [100.0, np.nan, 105.0, 110.0, np.nan],  # NaN at the end
            "equity": [10_000.0, 10_100.0, np.nan, 10_200.0, np.nan],  # NaN at the end
            "Volume": [1000] * 5,
        },
        index=dates,
    )
    trades_df = pd.DataFrame([{"pnl": np.nan, "costs": np.nan}])

    metrics = engine.compute_metrics(df, trades_df, 10_000.0)
    assert not np.isnan(metrics["buy_hold"])
    assert not np.isinf(metrics["buy_hold"])
    assert not np.isnan(metrics["cagr"])
    assert not np.isinf(metrics["cagr"])
    assert not np.isnan(metrics["total_return"])
    assert not np.isinf(metrics["total_return"])
    assert not np.isnan(metrics["max_drawdown"])
    assert not np.isnan(metrics["costs_total"])
    assert metrics["buy_hold"] == pytest.approx(0.10)  # (110.0 / 100.0 - 1)


def test_run_backtest_with_nan_bars():
    """Verify run_backtest safely handles DataFrames containing NaN rows."""
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    df = pd.DataFrame(
        {
            "Open": [100.0, np.nan, 105.0, 110.0, 115.0],
            "High": [101.0, np.nan, 106.0, 111.0, 116.0],
            "Low": [99.0, np.nan, 104.0, 109.0, 114.0],
            "Close": [100.0, np.nan, 105.0, 110.0, 115.0],
            "Volume": [1000, 0, 1000, 1000, 1000],
        },
        index=dates,
    )
    curve, trades = engine.run_backtest(df, strategy="sma_crossover", params={"fast": 2, "slow": 3})
    assert len(curve) == 4  # Dropped the NaN row
    assert not curve["equity"].isna().any()
    assert not curve["Close"].isna().any()
