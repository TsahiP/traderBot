from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
import config
import livebot


def test_livebot_run_watchlist_cycle():
    mock_trading = MagicMock()
    mock_data = MagicMock()

    # Configure open position for AAPL, no position for others
    def mock_get_open_pos(sym):
        if sym == "AAPL":
            mock_pos = MagicMock()
            mock_pos.qty = "10"
            mock_pos.avg_entry_price = "150.0"
            mock_pos.current_price = "155.0"
            return mock_pos
        raise Exception("Position not found")

    mock_trading.get_open_position.side_effect = mock_get_open_pos

    # Mock bars
    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    bars_df = pd.DataFrame(
        {
            "Open": [100.0] * 60,
            "High": [105.0] * 60,
            "Low": [95.0] * 60,
            "Close": [102.0] * 60,
            "Volume": [1000] * 60,
        },
        index=dates,
    )

    open_trades = {
        "AAPL": {"entry_date": "2026-01-01", "entry_price": 150.0, "qty": 10}
    }

    with patch("livebot.get_last_completed_bars", return_value=bars_df), \
         patch("livebot.compute_signals") as mock_compute, \
         patch("livebot.latest_signal") as mock_latest_sig, \
         patch("livebot.place_order") as mock_order, \
         patch("livebot.fill_price", return_value=155.0), \
         patch("livebot.notify_order"), \
         patch("livebot.record_trade"):

        # For AAPL, simulate SELL signal (-1) -> should sell AAPL and remove from open_trades
        # For other symbols, simulate 0 (no action)
        def mock_sig(df):
            return -1

        mock_latest_sig.side_effect = mock_sig

        with patch.object(config, "WATCHLIST", ["AAPL"]):
            control = livebot.telegram_bot.RuntimeControl()
            control.set_paused(True)
            updated_trades = livebot.run_watchlist_cycle(
                mock_trading, mock_data, open_trades, control
            )
            assert "AAPL" not in updated_trades
            assert mock_order.called
            mock_order.assert_called_with(mock_trading, "AAPL", "sell", qty=10)


def test_livebot_buy_signal_cycle():
    mock_trading = MagicMock()
    mock_data = MagicMock()
    mock_trading.get_open_position.side_effect = Exception("Position not found")

    dates = pd.date_range("2026-01-01", periods=60, freq="D")
    bars_df = pd.DataFrame(
        {
            "Open": [200.0] * 60,
            "High": [205.0] * 60,
            "Low": [195.0] * 60,
            "Close": [202.0] * 60,
            "Volume": [1000] * 60,
        },
        index=dates,
    )

    open_trades = {}

    with patch("livebot.get_last_completed_bars", return_value=bars_df), \
         patch("livebot.compute_signals"), \
         patch("livebot.latest_signal", return_value=1), \
         patch("livebot.place_order", return_value=MagicMock(id="ord_123")), \
         patch("livebot.fill_price", return_value=201.5), \
         patch("livebot.notify_order"):

        with patch.object(config, "WATCHLIST", ["NVDA"]):
            updated = livebot.run_watchlist_cycle(mock_trading, mock_data, open_trades)
            assert "NVDA" in updated
            assert updated["NVDA"]["entry_price"] == 201.5
            assert updated["NVDA"]["qty"] == config.QUANTITY


def test_livebot_paused_blocks_new_entry():
    mock_trading = MagicMock()
    mock_trading.get_open_position.side_effect = Exception("Position not found")
    bars_df = pd.DataFrame(
        {
            "Open": [200.0] * 60,
            "High": [205.0] * 60,
            "Low": [195.0] * 60,
            "Close": [202.0] * 60,
            "Volume": [1000] * 60,
        },
        index=pd.date_range("2026-01-01", periods=60, freq="D"),
    )
    control = livebot.telegram_bot.RuntimeControl()
    control.set_paused(True)

    with patch("livebot.get_last_completed_bars", return_value=bars_df), \
         patch("livebot.compute_signals"), \
         patch("livebot.latest_signal", return_value=1), \
         patch("livebot.place_order") as mock_order, \
         patch.object(config, "WATCHLIST", ["NVDA"]):
        updated = livebot.run_watchlist_cycle(
            mock_trading, MagicMock(), {}, control
        )

    assert updated == {}
    mock_order.assert_not_called()
