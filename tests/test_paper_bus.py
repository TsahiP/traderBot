"""paperbot event bus: store + fan-out, unstored quotes, replay, slow subscribers,
listeners that fail."""
import threading

import pytest

from paper.bus import EventBus
from paper.journal import Journal


@pytest.fixture
def bus(tmp_path):
    journal = Journal(tmp_path / "paperbot.db")
    yield EventBus(journal)
    journal.close()


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_publish_stores_and_reaches_every_subscriber(bus):
    a, b = bus.subscribe(), bus.subscribe()
    event = bus.publish("fill", {"qty": 10}, run_id="swing_sma", symbol="SPY")
    assert event["id"] == bus.journal.last_event_id()
    assert _drain(a) == [event] and _drain(b) == [event]
    assert bus.journal.events_since(0)[0]["payload"] == {"qty": 10}


def test_unstored_events_have_no_id_and_skip_the_journal(bus):
    q = bus.subscribe()
    quote = bus.publish("quote", {"price": 101.2}, symbol="SPY", store=False)
    assert quote["id"] is None and quote["ts"]
    assert _drain(q) == [quote]
    assert bus.journal.events_since(0) == []


def test_replay_returns_stored_events_after_an_id(bus):
    first = bus.publish("bot.start", {})
    bus.publish("quote", {}, store=False)
    second = bus.publish("scan.done", {"added": 3})
    assert [e["id"] for e in bus.replay(first["id"])] == [second["id"]]
    assert [e["type"] for e in bus.replay(0)] == ["bot.start", "scan.done"]


def test_a_full_subscriber_is_dropped_without_blocking_the_rest(bus):
    slow = bus.subscribe(maxsize=2)
    fast = bus.subscribe()
    for i in range(5):
        bus.publish("setup.state", {"i": i})
    assert not bus.is_subscribed(slow)
    assert bus.is_subscribed(fast)
    assert len(_drain(fast)) == 5
    assert len(_drain(slow)) == 2          # what fit; the client re-syncs via replay
    assert bus.subscriber_count() == 1


def test_unsubscribe_stops_delivery(bus):
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.unsubscribe(q)                     # twice is harmless
    bus.publish("fill", {})
    assert q.empty()


def test_listeners_run_and_a_failing_one_does_not_break_publish(bus, caplog):
    seen = []
    bus.add_listener(lambda e: (_ for _ in ()).throw(RuntimeError("discord down")))
    bus.add_listener(seen.append)
    event = bus.publish("trade.closed", {"r": 2.0})
    assert seen == [event]
    assert "discord down" in caplog.text


def test_publish_from_several_threads(bus):
    q = bus.subscribe(maxsize=1000)

    def worker():
        for _ in range(50):
            bus.publish("fill", {})

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = _drain(q)
    assert len(got) == 200
    assert sorted(e["id"] for e in got) == [e["id"] for e in bus.replay(0, limit=1000)]
