"""Discord webhook helpers and timeframe → route mapping."""
import json

import pytest

import config
import signals


@pytest.fixture(autouse=True)
def _discord_webhooks(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_DAY_TRADE", "https://example.com/day")
    monkeypatch.setenv("DISCORD_WEBHOOK_HOUR_TRADE", "https://example.com/hour")
    monkeypatch.setenv("DISCORD_WEBHOOK_MINUTE_TRADE", "https://example.com/minute")
    monkeypatch.setenv("DISCORD_WEBHOOK_WEEK_TRADE", "https://example.com/week")


def test_discord_route_for_symbol_timeframe_stock():
    assert signals.discord_route_for_symbol_timeframe("SPY", "1d") == "stock_1d"
    assert signals.discord_route_for_symbol_timeframe("AAPL", "5m") == "stock_5m"
    assert signals.discord_route_for_symbol_timeframe("BTC-USD", "1h") == "crypto_1h"
    assert signals.discord_route_for_symbol_timeframe("btc", "1m") == "crypto_1m"
    assert signals.discord_route_for_symbol_timeframe("ETH-USD", "5m") == "crypto_5m"
    assert signals.discord_route_for_symbol_timeframe("SOL-USD", "1m") == "crypto_1m"
    assert signals.discord_route_for_symbol_timeframe("NVDA", "1m") == "stock_1m"
    assert signals.discord_route_for_symbol_timeframe("SPY", "bogus") is None


def test_discord_route_for_timeframe_legacy_stock():
    assert signals.discord_route_for_timeframe("1d") == "stock_1d"
    assert signals.discord_route_for_timeframe("1h") == "stock_1h"
    assert signals.discord_route_for_timeframe("1m") == "stock_1m"
    assert signals.discord_route_for_timeframe("5m") == "stock_5m"
    assert signals.discord_route_for_timeframe("1w") == "stock_1w"


def test_normalize_discord_route_aliases():
    assert signals.normalize_discord_route("day") == "stock_1d"
    assert signals.normalize_discord_route("crypto_5m") == "crypto_5m"
    assert signals.normalize_discord_route("stock_news") == "stock_news"
    assert signals.normalize_discord_route("nope") is None


def test_discord_timeframes_available(monkeypatch):
    for route in signals.DISCORD_CANONICAL_ROUTES:
        for key in signals.DISCORD_ROUTE_ENV_FALLBACKS.get(route, ()):
            monkeypatch.delenv(key, raising=False)
    for key in signals.DISCORD_ROUTE_ENV.values():
        monkeypatch.delenv(key, raising=False)
    assert signals.discord_timeframes_available() == []
    monkeypatch.setenv("DISCORD_WEBHOOK_DAY_TRADE", "https://example.com/day")
    assert signals.discord_timeframes_available() == ["1d"]
    monkeypatch.setenv("DISCORD_WEBHOOK_MINUTE_TRADE", "https://example.com/min")
    available = signals.discord_timeframes_available()
    assert "1m" in available
    assert "1d" in available


def test_stock_intraday_webhook_fallback_chain(monkeypatch):
    for key in signals.DISCORD_ROUTE_ENV_FALLBACKS["stock_15m"]:
        monkeypatch.delenv(key, raising=False)
    assert signals.discord_webhook_url("stock_15m") is None
    monkeypatch.setenv("DISCORD_WEBHOOK_MINUTE_TRADE", "https://example.com/legacy-minute")
    assert signals.discord_webhook_url("stock_15m") == "https://example.com/legacy-minute"
    monkeypatch.setenv("DISCORD_WEBHOOK_STOCK_15M", "https://example.com/stock-15m")
    assert signals.discord_webhook_url("stock_15m") == "https://example.com/stock-15m"


def test_validate_allows_discord_tf_without_webhook(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_MINUTE_TRADE", raising=False)
    cfg = signals.validate_config({
        "lists": [{
            "id": "default",
            "name": "Default",
            "symbols": ["SPY"],
            "telegram_timeframes": ["1d"],
            "discord_timeframes": ["1m"],
            "patterns": ["bullish_engulfing"],
        }],
    })
    assert cfg["lists"][0]["discord_timeframes"] == ["1m"]


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
    monkeypatch.delenv("DISCORD_WEBHOOK_STOCK_1D", raising=False)
    signals.reload_discord_webhooks_cache(force=True)
    assert signals.discord_webhook_url("day") is None
    monkeypatch.setenv("DISCORD_WEBHOOK_DAY_TRADE", "  https://discord.com/api/webhooks/x/y  ")
    assert signals.discord_webhook_url("day") == "https://discord.com/api/webhooks/x/y"
    assert signals.discord_webhook_url("invalid") is None


def test_discord_webhook_url_from_sync_file(monkeypatch, tmp_path):
    for route in signals.DISCORD_CANONICAL_ROUTES:
        for key in signals.DISCORD_ROUTE_ENV_FALLBACKS.get(route, ()):
            monkeypatch.delenv(key, raising=False)
    path = tmp_path / "discord_webhooks.json"
    path.write_text(
        json.dumps({
            "routes": {
                "stock_5m": {
                    "url": "https://discord.com/api/webhooks/sync/5m",
                    "channel_id": "1",
                    "channel_name": "stock-5m",
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "DISCORD_WEBHOOKS_PATH", path)
    signals.reload_discord_webhooks_cache(force=True)
    assert signals.discord_webhook_url("stock_5m") == "https://discord.com/api/webhooks/sync/5m"
    monkeypatch.setenv("DISCORD_WEBHOOK_STOCK_5M", "https://discord.com/api/webhooks/env/5m")
    assert signals.discord_webhook_url("stock_5m") == "https://discord.com/api/webhooks/env/5m"


def test_discord_configured(monkeypatch):
    for route in signals.DISCORD_CANONICAL_ROUTES:
        for key in signals.DISCORD_ROUTE_ENV_FALLBACKS.get(route, ()):
            monkeypatch.delenv(key, raising=False)
    configured = signals.discord_configured()
    assert all(v is False for v in configured.values())
    monkeypatch.setenv("DISCORD_WEBHOOK_HOUR_TRADE", "https://example.com/hook")
    assert signals.discord_configured()["stock_1h"] is True
    assert signals.discord_configured()["crypto_1h"] is False


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
