from unittest.mock import MagicMock, patch

import config
import telegram_bot


def make_bot():
    control = telegram_bot.RuntimeControl(started_at=0)
    return telegram_bot.TelegramCommandBot(MagicMock(), control), control


def test_unauthorized_chat_is_ignored(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")
    bot, _ = make_bot()

    with patch("telegram_bot.notifier.send_telegram_to") as send:
        bot.handle_update(
            {"message": {"chat": {"id": 999}, "text": "/status"}}
        )

    send.assert_not_called()


def test_pause_resume_and_help(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")
    bot, control = make_bot()

    with patch("telegram_bot.notifier.send_telegram_to") as send:
        bot.handle_update(
            {"message": {"chat": {"id": 123}, "text": "/pause"}}
        )
        assert control.is_paused() is True
        bot.handle_update(
            {"message": {"chat": {"id": 123}, "text": "/resume"}}
        )
        assert control.is_paused() is False
        bot.handle_update(
            {"message": {"chat": {"id": 123}, "text": "/help"}}
        )

    assert send.call_count == 3
    assert "TradeBot commands" in send.call_args.args[1]


def test_status_and_pnl_formatting():
    bot, control = make_bot()
    control.set_listener_healthy(True)
    bot.trading.get_clock.return_value = MagicMock(is_open=True)
    snapshot = {
        "account": {
            "equity": 101000.0,
            "buying_power": 50000.0,
            "day_pnl": 1000.0,
        },
        "positions": [
            {
                "symbol": "SPY",
                "qty": 2.0,
                "current": 650.0,
                "unrealized_pl": -25.0,
            }
        ],
    }

    with patch("telegram_bot.alpaca_service.get_account_snapshot", return_value=snapshot):
        status = bot._status_text()
        pnl = bot._pnl_text()

    assert "Trading: ACTIVE" in status
    assert "SPY x2" in status
    assert "Account day P&L: +$1,000.00" in pnl
    assert "Open-position unrealized: -$25.00" in pnl


def test_update_offset_advances(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token")
    bot, _ = make_bot()
    response = MagicMock()
    response.json.return_value = {
        "ok": True,
        "result": [{"update_id": 7, "message": {"chat": {"id": 123}, "text": "/help"}}],
    }

    def stop_after_update(update):
        bot.stop_event.set()

    bot.handle_update = stop_after_update
    with patch("telegram_bot.requests.get", return_value=response) as get:
        bot.run()

    assert bot.offset == 8
    assert get.call_args.kwargs["params"]["offset"] is None


def test_backtest_success_and_lock_release():
    payload = {
        "meta": {
            "symbol": "SPY",
            "strategy_label": "SMA crossover",
            "timeframe": "1d",
        },
        "metrics": {
            "start": "2020-01-01",
            "end": "2026-01-01",
            "total_return": 0.25,
            "cagr": 0.04,
            "max_drawdown": -0.1,
            "trades": 8,
            "win_rate": 0.625,
            "costs_total": 12.0,
            "final_equity": 125000.0,
        },
    }
    runner = MagicMock(return_value=(payload, 200, None))
    control = telegram_bot.RuntimeControl()
    bot = telegram_bot.TelegramCommandBot(MagicMock(), control, runner)
    control.backtest_lock.acquire()

    with patch("telegram_bot.notifier.send_telegram_to") as send:
        bot._run_backtest("123", "SPY", "sma_crossover")

    assert "Return: 25.00%" in send.call_args.args[1]
    assert control.backtest_lock.acquire(blocking=False) is True
    control.backtest_lock.release()


def test_backtest_usage_and_busy_replies():
    bot, control = make_bot()

    with patch("telegram_bot.notifier.send_telegram_to") as send:
        bot._start_backtest("123", ["SPY"])
        assert "Usage:" in send.call_args.args[1]
        control.backtest_lock.acquire()
        bot._start_backtest("123", ["SPY", "sma_crossover"])
        assert "already running" in send.call_args.args[1]
        control.backtest_lock.release()
