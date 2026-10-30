"""Alpaca live streams for paperbot, each on its own daemon thread.

- `TradingStreamThread`: order updates (new, fill, partial_fill, canceled,
  rejected...) the moment they happen, so a stop or target that fills shows
  in the UI within a second instead of on the next cycle.
- `QuoteStreamThread`: IEX trades (free feed) for the symbols the bot is
  watching or holding - live P&L and distance to a trigger, never a decision.
  Runs only while there are symbols (StockDataStream spins while it has no
  subscription) and throttles each symbol to one quote a second.

Both push plain dicts onto the bot's inbox queue: `trade_update`, `quote`,
`stream_status` (connecting / down / stopped) and `stream_error`. alpaca-py
already reconnects a dropped websocket by itself; when `run()` itself fails
or ends, `StreamThread` builds a new stream after a backoff. `health()` reads
whether the stream's socket is up right now, for the heartbeat.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from paper.broker import _iso, _num, _val, normalize_order

logger = logging.getLogger("paperbot")

BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0)
HEALTHY_RUN_S = 60.0        # a run that lasted this long resets the backoff
STOP_TRIES = 20


def normalize_trade_update(raw) -> dict:
    return {"kind": "trade_update", "event": _val(raw.event), "order": normalize_order(raw.order),
            "price": _num(raw.price), "qty": _num(raw.qty), "position_qty": _num(raw.position_qty),
            "timestamp": _iso(raw.timestamp),
            "execution_id": str(raw.execution_id) if raw.execution_id else None}


def normalize_quote(raw) -> dict:
    return {"kind": "quote", "symbol": raw.symbol, "price": float(raw.price), "size": float(raw.size),
            "timestamp": _iso(raw.timestamp)}


def symbol_diff(current: set, wanted: set) -> tuple[list[str], list[str]]:
    """(to subscribe, to unsubscribe), sorted."""
    return sorted(wanted - current), sorted(current - wanted)


class StreamThread:
    """Runs `factory()`'s stream on a daemon thread; rebuilds it after a failure."""

    def __init__(self, name: str, factory: Callable, out_queue, backoff=BACKOFF_S,
                 configure: Callable | None = None):
        self.name = name
        self._factory = factory
        self._out = out_queue
        self._backoff = tuple(backoff)
        self._configure = configure or (lambda stream: None)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._stream = None
        self._state = "idle"

    def _status(self, status: str, detail: str = "") -> None:
        self._state = status
        self._out.put({"kind": "stream_status", "stream": self.name, "status": status, "detail": detail})
        log = logger.warning if status == "down" else logger.info
        log("%s stream %s %s", self.name, status, detail)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.is_alive():
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._loop, args=(self._stop_event,),
                                            name=f"paperbot-{self.name}", daemon=True)
            self._thread.start()

    def _loop(self, stop: threading.Event) -> None:
        attempt = 0
        while not stop.is_set():
            stream = self._factory()
            self._configure(stream)
            with self._lock:
                self._stream = stream
            self._status("connecting")
            started, error = time.monotonic(), None
            try:
                stream.run()
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            if stop.is_set():
                break
            if time.monotonic() - started >= HEALTHY_RUN_S:
                attempt = 0
            wait = self._backoff[min(attempt, len(self._backoff) - 1)]
            self._status("down", f"{error or 'stream ended'}; retry in {wait:g}s")
            attempt += 1
            stop.wait(wait)
        with self._lock:
            self._stream = None
        self._status("stopped")

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            stream, thread = self._stream, self._thread
        if thread is None:
            return
        for _ in range(STOP_TRIES):         # stop() fails until the stream's loop is up
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
            thread.join(0.1)
            if not thread.is_alive():
                return
            with self._lock:
                stream = self._stream
        logger.warning("%s stream did not stop in time", self.name)

    def health(self) -> str:
        """up / connecting / down / stopped / idle."""
        if not self.is_alive():
            return "stopped" if self._state == "stopped" else "idle"
        with self._lock:
            stream = self._stream
        return "up" if getattr(stream, "_running", False) else self._state


def _trading_factory(key: str, secret: str) -> Callable:
    def make():
        from alpaca.trading.stream import TradingStream

        return TradingStream(key, secret, paper=True)

    return make


def _quote_factory(key: str, secret: str) -> Callable:
    def make():
        from alpaca.data.enums import DataFeed
        from alpaca.data.live import StockDataStream

        return StockDataStream(key, secret, feed=DataFeed.IEX)

    return make


class TradingStreamThread(StreamThread):
    def __init__(self, out_queue, key: str | None = None, secret: str | None = None,
                 factory: Callable | None = None, backoff=BACKOFF_S):
        super().__init__("trading", factory or _trading_factory(key, secret), out_queue, backoff,
                         configure=lambda stream: stream.subscribe_trade_updates(self._on_update))

    async def _on_update(self, data) -> None:
        try:
            self._out.put(normalize_trade_update(data))
        except Exception as exc:
            self._out.put({"kind": "stream_error", "stream": self.name, "error": str(exc)})


class QuoteStreamThread(StreamThread):
    def __init__(self, out_queue, key: str | None = None, secret: str | None = None,
                 factory: Callable | None = None, backoff=BACKOFF_S, min_interval_s: float = 1.0,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__("quotes", factory or _quote_factory(key, secret), out_queue, backoff,
                         configure=self._subscribe_all)
        self._symbols: set[str] = set()
        self._sym_lock = threading.Lock()
        self._min_interval = min_interval_s
        self._clock = clock
        self._last: dict[str, float] = {}

    def _subscribe_all(self, stream) -> None:
        if self._symbols:
            stream.subscribe_trades(self._on_trade, *sorted(self._symbols))

    def set_symbols(self, wanted) -> None:
        """Watch exactly `wanted`; the stream starts and stops with the set."""
        wanted = set(wanted)
        with self._sym_lock:
            add, remove = symbol_diff(self._symbols, wanted)
            self._symbols = wanted
            if not wanted:
                if self.is_alive():
                    self.stop()
                return
            if not self.is_alive():
                self.start()
                return
            with self._lock:
                stream = self._stream
            if stream is None:
                return
            try:
                if add:
                    stream.subscribe_trades(self._on_trade, *add)
                if remove:
                    stream.unsubscribe_trades(*remove)
            except Exception as exc:
                logger.warning("quote subscription change failed (%s) - restarting the stream", exc)
                self.stop()
                self.start()

    def health(self) -> str:
        return "idle" if not self._symbols else super().health()

    async def _on_trade(self, data) -> None:
        try:
            now = self._clock()
            last = self._last.get(data.symbol)
            if last is not None and now - last < self._min_interval:
                return
            self._last[data.symbol] = now
            self._out.put(normalize_quote(data))
        except Exception as exc:
            self._out.put({"kind": "stream_error", "stream": self.name, "error": str(exc)})
