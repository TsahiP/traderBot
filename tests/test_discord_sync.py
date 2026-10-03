"""Discord webhook sync (mocked API)."""
import json
from unittest.mock import patch

import pytest

import config
import discord_sync


@pytest.fixture
def discord_env(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "guild-1")


def test_sync_maps_channels_and_writes_file(monkeypatch, tmp_path, discord_env):
    path = tmp_path / "discord_webhooks.json"
    monkeypatch.setattr(config, "DISCORD_WEBHOOKS_PATH", path)

    channels = [
        {"id": "cat1", "type": 4, "name": "Stocks"},
        {"id": "ch1", "type": 0, "name": "stock-1h", "parent_id": "cat1"},
        {"id": "ch2", "type": 0, "name": "random-chat", "parent_id": "cat1"},
    ]
    webhooks = [{"id": "wh1", "name": "TradeBot Signals", "url": "https://discord.com/api/webhooks/a/b"}]

    def fake_request(method, url_path, token, *, json_body=None):
        if url_path == "/guilds/guild-1/channels":
            return channels
        if url_path == "/channels/ch1/webhooks":
            return webhooks
        raise AssertionError(f"unexpected {method} {url_path}")

    with patch.object(discord_sync, "_request", side_effect=fake_request):
        result = discord_sync.sync_discord_webhooks()

    assert "stock_1h" in result["routes"]
    assert result["routes"]["stock_1h"]["url"] == "https://discord.com/api/webhooks/a/b"
    assert "random-chat" in result["unmatched_channels"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["routes"]["stock_1h"]["channel_name"] == "stock-1h"


def test_sync_requires_credentials(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_GUILD_ID", raising=False)
    with pytest.raises(discord_sync.DiscordSyncError):
        discord_sync.sync_discord_webhooks()
