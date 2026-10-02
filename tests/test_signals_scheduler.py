"""Per-watchlist job expansion and bar-aligned next_run."""
from datetime import datetime

import pytest

import signals
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _discord_day_webhook(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_DAY_TRADE", "https://example.com/day")


def test_expand_scan_jobs_per_watchlist():
    cfg = {
        "lists": [
            {
                "id": "a",
                "name": "A",
                "symbols": ["SPY"],
                "telegram_timeframes": ["1d"],
                "discord_timeframes": [],
                "patterns": [],
            },
            {
                "id": "b",
                "name": "B",
                "symbols": ["QQQ"],
                "telegram_timeframes": ["1m"],
                "discord_timeframes": [],
                "patterns": [],
            },
        ],
    }
    jobs = signals.expand_scan_jobs(cfg)
    assert ("a", "A", "SPY", "1d") in jobs
    assert ("b", "B", "QQQ", "1m") in jobs
    assert len(jobs) == 2


def test_signal_key_includes_list_id():
    k = signals.signal_key("list-a", "SPY", "1d", "2026-01-01", "bullish_engulfing")
    assert k.startswith("list-a|")


def test_next_run_1m_advances():
    after = datetime(2026, 1, 15, 10, 3, 20, tzinfo=NY)
    nxt = signals.next_run_after_timeframe("1m", after)
    assert nxt > after
    assert nxt.minute == 4
    assert nxt.second == signals.BAR_CLOSE_OFFSET_SECONDS


def test_next_run_1d_advances():
    after = datetime(2026, 1, 15, 14, 0, 0, tzinfo=NY)
    nxt = signals.next_run_after_timeframe("1d", after)
    assert nxt.date().day == 16


def test_validate_config_no_poll_minutes():
    cfg = signals.validate_config({
        "lists": [{
            "id": "default",
            "name": "Default",
            "symbols": ["SPY"],
            "telegram_timeframes": ["1d"],
            "discord_timeframes": ["1d"],
            "patterns": ["bullish_engulfing"],
        }],
    })
    assert "poll_minutes" not in cfg
