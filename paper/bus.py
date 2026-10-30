"""paperbot event bus: every event is stored in the journal, then pushed to each
SSE subscriber and listener (the Discord notifier), in id order.

Storing and fanning out happen under one lock, so subscribers always see
events in the order of their ids even when several threads publish. A
subscriber whose queue fills up (a stalled browser tab) is dropped instead of
blocking the bot; the server ends that stream and the browser reconnects with
`Last-Event-ID`, which `replay` serves from the journal - nothing is lost.

`store=False` is for high-rate display-only events (live quotes): they reach
subscribers with `id: None` and never touch the journal. Listeners run inside
the lock and must not block (the notifier only enqueues).
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from paper.journal import Journal, now_iso

logger = logging.getLogger("paperbot")

DEFAULT_QUEUE_SIZE = 1000


class EventBus:
    def __init__(self, journal: Journal):
        self.journal = journal
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._listeners: list[Callable[[dict], None]] = []

    def publish(self, type: str, payload: dict | None = None, run_id: str | None = None,
                symbol: str | None = None, store: bool = True) -> dict:
        with self._lock:
            if store:
                event = self.journal.add_event(type, payload, run_id=run_id, symbol=symbol)
            else:
                event = {"id": None, "ts": now_iso("milliseconds"), "type": type,
                         "run_id": run_id, "symbol": symbol, "payload": payload or {}}
            for q in list(self._subscribers):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    self._subscribers.remove(q)
                    logger.warning("SSE subscriber fell behind - dropped (it re-syncs on reconnect)")
            for listener in self._listeners:
                try:
                    listener(event)
                except Exception as exc:
                    logger.error("Event listener failed on %s: %s", type, exc)
        return event

    def subscribe(self, maxsize: int = DEFAULT_QUEUE_SIZE) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def is_subscribed(self, q: queue.Queue) -> bool:
        with self._lock:
            return q in self._subscribers

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def replay(self, last_id: int, limit: int = 500) -> list[dict]:
        """Stored events after `last_id` (SSE `Last-Event-ID` catch-up)."""
        return self.journal.events_since(last_id, limit=limit)
