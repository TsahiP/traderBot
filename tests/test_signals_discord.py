"""Discord webhook helpers and discord_route on watchlists."""
import pytest

import signals


def test_validate_accepts_discord_route():
    cfg = signals.validate_config({
        "poll_minutes": 5,
        "lists": [{
            "id": "day",
            "name": "Day",
            "symbols": ["SPY"],
            "timeframes": ["1d"],
            "patterns": ["bullish_engulfing"],
            "discord_route": "day",
        }],
    })
    assert cfg["lists"][0]["discord_route"] == "day"


def test_validate_omits_null_discord_route():
    cfg = signals.validate_config({
        "poll_minutes": 5,
        "lists": [{
            "id": "default",
            "name": "Default",
            "symbols": ["SPY"],
            "timeframes": ["1d"],
            "patterns": ["bullish_engulfing"],
            "discord_route": None,
        }],
    })
    assert "discord_route" not in cfg["lists"][0]


def test_validate_rejects_bad_discord_route():
    with pytest.raises(ValueError, match="discord_route"):
        signals.validate_config({
            "poll_minutes": 5,
            "lists": [{
                "id": "default",
                "name": "Default",
                "symbols": ["SPY"],
                "timeframes": ["1d"],
                "patterns": ["bullish_engulfing"],
                "discord_route": "monthly",
            }],
        })


def test_discord_webhook_url(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_DAY_TRADE", raising=False)
    assert signals.discord_webhook_url("day") is None
    monkeypatch.setenv("DISCORD_WEBHOOK_DAY_TRADE", "  https://discord.com/api/webhooks/x/y  ")
    assert signals.discord_webhook_url("day") == "https://discord.com/api/webhooks/x/y"
    assert signals.discord_webhook_url("invalid") is None


def test_discord_configured(monkeypatch):
    for key in signals.DISCORD_ROUTE_ENV.values():
        monkeypatch.delenv(key, raising=False)
    assert signals.discord_configured() == {"day": False, "hour": False, "week": False}
    monkeypatch.setenv("DISCORD_WEBHOOK_HOUR_TRADE", "https://example.com/hook")
    assert signals.discord_configured() == {"day": False, "hour": True, "week": False}


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
