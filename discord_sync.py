"""Sync Discord channel webhooks into output/discord_webhooks.json by channel name."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import requests

import config
import signals
from discord_channel_routes import parse_channel_route

DISCORD_API = "https://discord.com/api/v10"
GUILD_TEXT = 0
GUILD_CATEGORY = 4
WEBHOOK_NAME = "TradeBot Signals"


class DiscordSyncError(Exception):
    pass


def discord_bot_credentials() -> tuple[str, str]:
    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    guild_id = (os.getenv("DISCORD_GUILD_ID") or "").strip()
    if not token or not guild_id:
        raise DiscordSyncError("DISCORD_BOT_TOKEN and DISCORD_GUILD_ID must be set in .env")
    return token, guild_id


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}"}


def _request(
    method: str,
    path: str,
    token: str,
    *,
    json_body: dict | None = None,
) -> Any:
    url = f"{DISCORD_API}{path}"
    r = requests.request(method, url, headers=_headers(token), json=json_body, timeout=30)
    if r.status_code >= 400:
        detail = r.text[:500]
        raise DiscordSyncError(f"Discord API {method} {path} failed ({r.status_code}): {detail}")
    if r.status_code == 204 or not r.content:
        return None
    return r.json()


def _webhook_url(webhook: dict) -> str | None:
    url = (webhook.get("url") or "").strip()
    return url or None


def _pick_or_create_webhook(
    token: str,
    channel_id: str,
    *,
    create_missing: bool,
) -> str | None:
    hooks = _request("GET", f"/channels/{channel_id}/webhooks", token) or []
    for hook in hooks:
        if (hook.get("name") or "").strip() == WEBHOOK_NAME:
            return _webhook_url(hook)
    for hook in hooks:
        url = _webhook_url(hook)
        if url:
            return url
    if not create_missing:
        return None
    created = _request(
        "POST",
        f"/channels/{channel_id}/webhooks",
        token,
        json_body={"name": WEBHOOK_NAME},
    )
    return _webhook_url(created or {})


def sync_discord_webhooks(*, create_missing_webhooks: bool = False) -> dict:
    """Fetch guild channels, map names to routes, persist webhook URLs."""
    token, guild_id = discord_bot_credentials()
    channels = _request("GET", f"/guilds/{guild_id}/channels", token) or []

    categories = {
        ch["id"]: ch.get("name", "")
        for ch in channels
        if ch.get("type") == GUILD_CATEGORY
    }

    route_entries: dict[str, dict] = {}
    conflicts: list[dict] = []
    unmatched_channels: list[str] = []
    skipped_no_webhook: list[str] = []

    for ch in channels:
        if ch.get("type") != GUILD_TEXT:
            continue
        ch_name = ch.get("name") or ""
        parent = categories.get(ch.get("parent_id") or "", "")
        route = parse_channel_route(ch_name, parent)
        if not route:
            unmatched_channels.append(ch_name)
            continue

        channel_id = ch["id"]
        url = _pick_or_create_webhook(
            token,
            channel_id,
            create_missing=create_missing_webhooks,
        )
        if not url:
            skipped_no_webhook.append(ch_name)
            continue

        entry = {
            "url": url,
            "channel_id": channel_id,
            "channel_name": ch_name,
        }
        if route in route_entries:
            conflicts.append({
                "route": route,
                "channels": [route_entries[route]["channel_name"], ch_name],
            })
            continue
        route_entries[route] = entry

    payload = {
        "synced_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "routes": route_entries,
        "unmatched_channels": sorted(unmatched_channels),
        "conflicts": conflicts,
        "skipped_no_webhook": sorted(skipped_no_webhook),
    }
    signals._atomic_write(
        config.DISCORD_WEBHOOKS_PATH,
        json.dumps(payload, indent=2),
    )
    signals.reload_discord_webhooks_cache(force=True)
    return payload


def read_sync_snapshot() -> dict | None:
    path = config.DISCORD_WEBHOOKS_PATH
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def main() -> None:
    result = sync_discord_webhooks()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
