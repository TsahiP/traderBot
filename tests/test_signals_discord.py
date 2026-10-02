"""Discord webhook helpers and timeframe → route mapping."""
import pytest

import signals


@pytest.fixture(autouse=True)
def _discord_webhooks(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_DAY_TRADE", "https://example.com/day")
    monkeypatch.setenv("DISCORD_WEBHOOK_HOUR_TRADE", "https://example.com/hour")
    monkeypatch.setenv("DISCORD_WEBHOOK_MINUTE_TRADE", "https://example.com/minute")
    monkeypatch.setenv("DISCORD_WEBHOOK_WEEK_TRADE", "https://example.com/week")


def test_discord_route_for_timeframe():
    assert signals.discord_route_for_timeframe("1d") == "day"
    assert signals.discord_route_for_timeframe("1h") == "hour"
    assert signals.discord_route_for_timeframe("1m") == "minute"
    assert signals.discord_route_for_timeframe("5m") == "minute"
    assert signals.discord_route_for_timeframe("1w") == "week"
    assert signals.discord_route_for_timeframe("bogus") is None


def test_discord_timeframes_available(monkeypatch):
    for key in signals.DISCORD_ROUTE_ENV.values():
        monkeypatch.delenv(key, raising=False)
    assert signals.discord_timeframes_available() == []
    monkeypatch.setenv("DISCORD_WEBHOOK_DAY_TRADE", "https://example.com/day")
    assert signals.discord_timeframes_available() == ["1d"]
    monkeypatch.setenv("DISCORD_WEBHOOK_MINUTE_TRADE", "https://example.com/min")
    assert "1m" in signals.discord_timeframes_available()
    assert "1d" in signals.discord_timeframes_available()


def test_validate_rejects_discord_tf_without_webhook(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_MINUTE_TRADE", raising=False)
    with pytest.raises(ValueError, match="Discord webhook not configured"):
        signals.validate_config({
            "lists": [{
                "id": "default",
                "name": "Default",
                "symbols": ["SPY"],
                "telegram_timeframes": ["1d"],
                "discord_timeframes": ["1m"],
                "patterns": ["bullish_engulfing"],
            }],
        })


def test_validate_allows_empty_discord_timeframes():
    cfg = signals.validate_config({
        "lists": [{
            "id": "default",
            "name": "Default",
            "symbols": ["SPY"],
            "telegram_timeframes": ["1d"],
            "discord_timeframes": [],
            "patterns": ["bullish_engulfing"],
        }],
    })
    assert cfg["lists"][0]["discord_timeframes"] == []


def test_validate_strips_legacy_discord_route():
    cfg = signals.validate_config({
        "lists": [{
            "id": "default",
            "name": "Default",
            "symbols": ["SPY"],
            "telegram_timeframes": ["1d"],
            "discord_timeframes": ["1d"],
            "patterns": ["bullish_engulfing"],
            "discord_route": "day",
        }],
    })
    assert "discord_route" not in cfg["lists"][0]


def test_discord_webhook_url(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_DAY_TRADE", raising=False)
    assert signals.discord_webhook_url("day") is None
    monkeypatch.setenv("DISCORD_WEBHOOK_DAY_TRADE", "  https://discord.com/api/webhooks/x/y  ")
    assert signals.discord_webhook_url("day") == "https://discord.com/api/webhooks/x/y"
    assert signals.discord_webhook_url("invalid") is None


def test_discord_configured(monkeypatch):
    for key in signals.DISCORD_ROUTE_ENV.values():
        monkeypatch.delenv(key, raising=False)
    assert signals.discord_configured() == {
        "day": False, "hour": False, "minute": False, "week": False,
    }
    monkeypatch.setenv("DISCORD_WEBHOOK_HOUR_TRADE", "https://example.com/hook")
    assert signals.discord_configured() == {
        "day": False, "hour": True, "minute": False, "week": False,
    }


def test_validate_accepts_discord_weekly_timeframe():
    cfg = signals.validate_config({
        "lists": [{
            "id": "default",
            "name": "Default",
            "symbols": ["SPY"],
            "telegram_timeframes": ["1d"],
            "discord_timeframes": ["1w", "1d"],
            "patterns": ["bullish_engulfing"],
        }],
    })
    assert cfg["lists"][0]["discord_timeframes"] == ["1w", "1d"]

    legacy = signals.validate_config({
        "lists": [{
            "id": "default",
            "name": "Default",
            "symbols": ["SPY"],
            "telegram_timeframes": ["1d"],
            "discord_timeframes": ["w"],
            "patterns": ["bullish_engulfing"],
        }],
    })
    assert legacy["lists"][0]["discord_timeframes"] == ["1w"]


def test_dc_send_text(monkeypatch):
    class Resp:
        ok = True

    def fake_post(url, **kwargs):
        assert url == "https://example.com/hook"
        assert kwargs.get("json") == {"content": "hello"}
        return Resp()

    monkeypatch.setattr("requests.post", fake_post)
    assert signals.dc_send("https://example.com/hook", text="hello")


def test_dc_send_failure(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net")))
    assert not signals.dc_send("https://example.com/hook", text="hello")
