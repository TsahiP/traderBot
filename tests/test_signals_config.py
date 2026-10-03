"""Watchlist config: new lists shape, flat-file migration, validation."""
import json

import pytest

import config
import signals
from candle_patterns import PATTERNS


def _patch_path(monkeypatch, tmp_path):
    path = tmp_path / "signal_config.json"
    monkeypatch.setattr(config, "SIGNAL_CONFIG_PATH", path)
    return path


@pytest.fixture(autouse=True)
def _discord_webhooks(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_DAY_TRADE", "https://example.com/day")
    monkeypatch.setenv("DISCORD_WEBHOOK_HOUR_TRADE", "https://example.com/hour")
    monkeypatch.setenv("DISCORD_WEBHOOK_MINUTE_TRADE", "https://example.com/minute")
    monkeypatch.setenv("DISCORD_WEBHOOK_WEEK_TRADE", "https://example.com/week")


def _list(**overrides):
    item = {
        "id": "default",
        "name": "Default",
        "symbols": ["SPY"],
        "telegram_timeframes": ["1d"],
        "discord_timeframes": ["1d"],
        "patterns": ["bullish_engulfing"],
    }
    item.update(overrides)
    return item


def test_validate_maps_crypto_spot_aliases():
    cfg = signals.validate_config({
        "lists": [_list(symbols=["btc", "ETH", "SPY"])],
    })
    assert cfg["lists"][0]["symbols"] == ["BTC-USD", "ETH-USD", "SPY"]


def test_validate_accepts_lists():
    cfg = signals.validate_config({
        "lists": [
            _list(),
            _list(
                id="tech",
                name="Tech",
                symbols=["aapl", "AAPL", "msft"],
                telegram_timeframes=["1h", "1d"],
                discord_timeframes=["1d"],
            ),
        ],
    })
    assert cfg["lists"][1]["symbols"] == ["AAPL", "MSFT"]
    assert cfg["lists"][1]["telegram_timeframes"] == ["1h", "1d"]
    assert cfg["lists"][1]["discord_timeframes"] == ["1d"]


def test_validate_legacy_shared_timeframes():
    cfg = signals.validate_config({
        "lists": [{
            "id": "default",
            "name": "Default",
            "symbols": ["SPY"],
            "timeframes": ["5m", "1h"],
            "patterns": ["bullish_engulfing"],
        }],
    })
    assert cfg["lists"][0]["telegram_timeframes"] == ["5m", "1h"]
    assert cfg["lists"][0]["discord_timeframes"] == ["5m", "1h"]
    assert "timeframes" not in cfg["lists"][0]


def test_watchlist_scan_timeframes_union():
    wl = {
        "telegram_timeframes": ["1h", "1d"],
        "discord_timeframes": ["15m", "1d"],
    }
    assert signals.watchlist_scan_timeframes(wl) == ["1h", "1d", "15m"]


def test_load_migrates_flat_file(monkeypatch, tmp_path):
    path = _patch_path(monkeypatch, tmp_path)
    flat = {
        "symbols": ["qqq"],
        "timeframes": ["1h"],
        "patterns": ["morning_star"],
        "poll_minutes": 10,
    }
    path.write_text(json.dumps(flat), encoding="utf-8")

    cfg = signals.load_config()

    assert "poll_minutes" not in cfg
    assert len(cfg["lists"]) == 1
    assert cfg["lists"][0]["id"] == "default"
    assert cfg["lists"][0]["name"] == "Default"
    assert cfg["lists"][0]["symbols"] == ["QQQ"]
    assert cfg["lists"][0]["telegram_timeframes"] == ["1h"]
    assert cfg["lists"][0]["discord_timeframes"] == ["1h"]
    assert cfg["lists"][0]["patterns"] == ["morning_star"]
    assert json.loads(path.read_text(encoding="utf-8")) == flat


def test_validate_rejects_bad_lists():
    with pytest.raises(ValueError, match="At least one watchlist"):
        signals.validate_config({"lists": []})

    with pytest.raises(ValueError, match="Duplicate watchlist name"):
        signals.validate_config({
            "lists": [_list(name="Tech"), _list(id="tech2", name="tech")],
        })

    with pytest.raises(ValueError, match="Invalid symbol"):
        signals.validate_config({
            "lists": [_list(symbols=["BAD SYMBOL"])],
        })

    with pytest.raises(ValueError, match="Unknown pattern"):
        signals.validate_config({
            "lists": [_list(patterns=["not_a_pattern"])],
        })

    with pytest.raises(ValueError, match="Telegram timeframe"):
        signals.validate_config({
            "lists": [_list(telegram_timeframes=[])],
        })


def test_default_config_covers_every_pattern():
    cfg = signals.default_config()
    assert cfg["lists"][0]["patterns"] == list(PATTERNS.keys())
    assert cfg["lists"][0]["telegram_timeframes"] == ["1d"]
    assert cfg["lists"][0]["discord_timeframes"] == ["1d"]
