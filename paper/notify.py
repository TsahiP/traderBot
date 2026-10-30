"""Discord notifications for paperbot: a bus listener that posts the events
worth a ping (entries, closed trades, proposals, errors, start/stop) to the
paper-desk webhook, so you know what happened while away from the screen.

- Which events: `discord.events` in the config (proposal, fill, trade.closed,
  error, bot.offline, day.summary). `fill` posts entries only - an exit is
  the `trade.closed` message, with its P&L and R.
- Where: `DISCORD_WEBHOOK_<ROUTE>` (DISCORD_WEBHOOK_PAPER) in .env, else the
  route synced from paperbot's own channel, `#paperbot` (also `#paper-desk`):
  create it in Discord, then Sync from Discord creates the webhook.
- Never in the way: the listener only enqueues; a worker thread sends with
  retries; a failure is logged, never raised. The same error text is posted
  at most once per ERROR_REPEAT_S.
- Proposals say to approve in the Paper desk - webhooks cannot take a click.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from datetime import datetime
from typing import Callable

logger = logging.getLogger("paperbot")

BACKOFF_S = (2.0, 5.0, 15.0)
ERROR_REPEAT_S = 600.0
QUEUE_SIZE = 200
# config `discord.events` name -> bus event types it covers
EVENT_TYPES = {
    "proposal": ("proposal.created",),
    "fill": ("fill",),
    "trade.closed": ("trade.closed",),
    "error": ("error",),
    "bot.offline": ("bot.start", "bot.stop"),
    "day.summary": ("day.summary",),
}


def webhook_url(route: str) -> str | None:
    url = (os.getenv(f"DISCORD_WEBHOOK_{route.upper()}") or "").strip()
    if url:
        return url
    import signals

    return signals._webhook_url_from_sync_file(route)


def _money(x) -> str:
    return f"{x:+,.2f}"


def _hhmm(iso: str | None) -> str:
    try:
        return f"{datetime.fromisoformat(iso):%a %H:%M}"
    except (TypeError, ValueError):
        return "?"


def format_event(event: dict) -> str | None:
    """Discord text for one bus event, or None when it is not worth a message."""
    t, p = event["type"], event.get("payload") or {}
    sym, run = event.get("symbol"), event.get("run_id")
    tag = f" · {run}" if run else ""
    if t == "fill":
        if p.get("kind") != "entry":
            return None
        levels = ""
        if p.get("stop") is not None:
            levels += f" · stop {p['stop']:.2f}"
        if p.get("target") is not None:
            levels += f" · target {p['target']:.2f}"
        return f"🟢 Entered {p['side']} {p['qty']:g} {sym} @ {p['entry_price']:.2f}{levels}{tag}"
    if t == "trade.closed":
        icon = "🟢" if (p.get("pnl") or 0) >= 0 else "🔴"
        r = f" ({p['r']:+.2f}R)" if p.get("r") is not None else ""
        return (f"{icon} Closed {p['side']} {p['qty']:g} {sym} by {p['exit_reason']} @ {p['exit_price']:.2f}: "
                f"{_money(p['pnl'])}{r}{tag}")
    if t == "proposal.created":
        return (f"🟡 Proposal #{p['id']}{tag}: {p['ticket']} - approve or reject in the Paper desk "
                f"(expires {_hhmm(p.get('expires_at'))} NY)")
    if t == "error":
        return f"⚠️ paperbot: {p.get('message', 'error')}"
    if t == "bot.start":
        return f"▶️ paperbot started (pid {p.get('pid')})"
    if t == "bot.stop":
        return f"⏹️ paperbot stopped (pid {p.get('pid')})"
    if t == "day.summary":
        return f"📊 {p.get('text', 'day summary')}"
    return None


class DiscordNotifier:
    def __init__(self, get_cfg: Callable[[], dict], resolve: Callable[[str], str | None] = webhook_url,
                 send: Callable | None = None, backoff=BACKOFF_S, sleep=time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if send is None:
            from signals import dc_send as send
        self._get_cfg = get_cfg
        self._resolve = resolve
        self._send = send
        self._backoff = tuple(backoff)
        self._sleep = sleep
        self._clock = clock
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self._recent_errors: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.configured: bool | None = None

    def _wanted(self, event_type: str) -> bool:
        names = self._get_cfg()["discord"]["events"]
        return any(event_type in EVENT_TYPES.get(n, ()) for n in names)

    def __call__(self, event: dict) -> None:
        """Bus listener: decide and enqueue, never block."""
        if not self._wanted(event["type"]):
            return
        text = format_event(event)
        if text is None:
            return
        if event["type"] == "error":
            now = self._clock()
            last = self._recent_errors.get(text)
            if last is not None and now - last < ERROR_REPEAT_S:
                return
            self._recent_errors[text] = now
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            logger.warning("Discord queue full - dropped: %s", text[:80])

    def start(self) -> None:
        self._thread = threading.Thread(target=self._work, name="paperbot-discord", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout)

    def _work(self) -> None:
        while not self._stop.is_set():
            text = self._queue.get()
            if text is None:
                return
            url = self._resolve(self._get_cfg()["discord"]["route"])
            if not url:
                if self.configured is not False:
                    logger.info("No Discord webhook for the paper desk - notifications off")
                self.configured = False
                continue
            self.configured = True
            for attempt in range(len(self._backoff) + 1):
                try:
                    if self._send(url, text, suppress_embeds=True):
                        break
                except Exception as exc:
                    logger.warning("Discord send failed: %s", exc)
                if attempt < len(self._backoff):
                    self._sleep(self._backoff[attempt])
            else:
                logger.error("Discord gave up on: %s", text[:80])
