from unittest.mock import MagicMock
import alpaca_service


def test_credential_checks(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "test_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
    assert alpaca_service.has_alpaca_credentials() is True
    assert alpaca_service.get_alpaca_credentials() == ("test_key", "test_secret")

    monkeypatch.setenv("ALPACA_API_KEY", "")
    assert alpaca_service.has_alpaca_credentials() is False


def test_fetch_fill_price_retry():
    mock_trading = MagicMock()
    # First call returns no filled price, second call returns 150.25
    order_pending = MagicMock(filled_avg_price=None)
    order_filled = MagicMock(filled_avg_price="150.25")
    mock_trading.get_order_by_id.side_effect = [order_pending, order_filled]

    price = alpaca_service.fetch_fill_price(mock_trading, "order_123", fallback_price=149.0, retry_delay=0.01)
    assert price == 150.25


def test_fetch_fill_price_fallback():
    mock_trading = MagicMock()
    mock_trading.get_order_by_id.side_effect = Exception("Order not found")

    price = alpaca_service.fetch_fill_price(mock_trading, "order_123", fallback_price=149.0, max_retries=2, retry_delay=0.01)
    assert price == 149.0


def test_account_snapshot_includes_daily_pnl():
    mock_trading = MagicMock()
    mock_trading.get_account.return_value = MagicMock(
        equity="102500.00",
        last_equity="100000.00",
        cash="25000.00",
        buying_power="50000.00",
    )
    mock_trading.get_all_positions.return_value = []

    snapshot = alpaca_service.get_account_snapshot(mock_trading)

    assert snapshot["account"]["last_equity"] == 100000.0
    assert snapshot["account"]["day_pnl"] == 2500.0
