"""paper.server: /health, /state, /commands validation, SSE framing and replay, CORS."""
import json
import threading
import time
from types import SimpleNamespace as NS

import pytest

from paper.bus import EventBus
from paper.journal import Journal
from paper.server import create_app, sse_frame


@pytest.fixture
def env(tmp_path):
    journal = Journal(tmp_path / "paperbot.db")
    bus = EventBus(journal)
    posted = []
    bot = NS(snapshot={"ts": "t", "paused": False, "positions": []}, pid=4242, post=posted.append,
             journal=journal, bus=bus)
    app = create_app(bot, ping_s=0.05)
    yield app.test_client(), bot, posted, journal, bus
    journal.close()


def test_health_and_state(env):
    client, bot, _, _, _ = env
    h = client.get("/health").get_json()
    assert h["ok"] is True and h["pid"] == 4242 and h["uptime_s"] >= 0
    assert client.get("/state").get_json() == bot.snapshot
    bot.snapshot = {}
    assert client.get("/state").get_json() == {"starting": True}


@pytest.mark.parametrize("body", [
    {"kind": "pause"},
    {"kind": "resume", "args": {}},
    {"kind": "flatten_all"},
    {"kind": "close_position", "args": {"symbol": "spy"}},
    {"kind": "set_approval", "args": {"mode": "manual"}},
    {"kind": "approve", "args": {"proposal_id": 7}},
    {"kind": "reject", "args": {"proposal_id": "7"}},
])
def test_good_commands_are_journaled_and_queued(env, body):
    client, _, posted, journal, _ = env
    resp = client.post("/commands", json=body)
    assert resp.status_code == 202
    out = resp.get_json()
    assert out["status"] == "queued"
    row = journal.command(out["command_id"])
    assert row["source"] == "ui" and row["kind"] == body["kind"] and row["status"] == "queued"
    assert posted[-1] == {"kind": "command", "id": out["command_id"], "cmd": body["kind"], "args": row["args"]}


def test_commands_are_normalised(env):
    client, _, posted, _, _ = env
    client.post("/commands", json={"kind": "close_position", "args": {"symbol": " spy "}})
    client.post("/commands", json={"kind": "reject", "args": {"proposal_id": "7"}})
    assert posted[0]["args"] == {"symbol": "SPY"} and posted[1]["args"] == {"proposal_id": 7}


@pytest.mark.parametrize("body,needle", [
    ({"kind": "launch"}, "kind"),
    ({"kind": "close_position", "args": {}}, "symbol"),
    ({"kind": "close_position", "args": {"symbol": "NOT A TICKER"}}, "symbol"),
    ({"kind": "set_approval", "args": {"mode": "sometimes"}}, "mode"),
    ({"kind": "approve", "args": {"proposal_id": "x"}}, "proposal_id"),
    ({"kind": "pause", "args": {"extra": 1}}, "extra"),
    ("not an object", "object"),
])
def test_bad_commands_are_refused(env, body, needle):
    client, _, posted, journal, _ = env
    resp = client.post("/commands", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 400 and needle in resp.get_json()["error"]
    assert posted == []


def test_sse_frame():
    assert sse_frame({"id": 5, "type": "fill", "payload": {}}) == \
        'id: 5\ndata: {"id": 5, "type": "fill", "payload": {}}\n\n'
    assert sse_frame({"id": None, "type": "quote"}).startswith("data: ")


def _read(resp, n, timeout=2.0):
    """The first `n` SSE data frames of a streamed response."""
    frames, buf, end = [], "", time.monotonic() + timeout
    it = iter(resp.response)
    while len(frames) < n and time.monotonic() < end:
        buf += next(it).decode()
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            data = [line[6:] for line in block.splitlines() if line.startswith("data: ")]
            if data:
                frames.append(json.loads(data[0]))
    resp.close()
    return frames


def test_stream_replays_after_last_event_id_then_goes_live(env):
    client, _, _, _, bus = env
    first = bus.publish("bot.start", {})
    bus.publish("scan.done", {"n": 1})
    bus.publish("fill", {"qty": 10})
    resp = client.get("/stream", headers={"Last-Event-ID": str(first["id"])}, buffered=False)
    assert resp.mimetype == "text/event-stream"
    threading.Timer(0.1, lambda: bus.publish("quote", {"price": 1.0}, store=False)).start()
    frames = _read(resp, 3)
    assert [f["type"] for f in frames] == ["scan.done", "fill", "quote"]


def test_stream_after_query_parameter_and_no_duplicates(env):
    client, _, _, _, bus = env
    bus.publish("bot.start", {})
    resp = client.get("/stream?after=0", buffered=False)
    threading.Timer(0.1, lambda: bus.publish("fill", {"qty": 1})).start()
    frames = _read(resp, 2)
    assert [f["type"] for f in frames] == ["bot.start", "fill"]
    assert len({f["id"] for f in frames}) == 2


def test_stream_sends_pings_and_unsubscribes_on_close(env):
    client, _, _, _, bus = env
    resp = client.get("/stream", buffered=False)
    it = iter(resp.response)
    chunks = [next(it).decode() for _ in range(3)]
    assert any(c.startswith(": ping") for c in chunks)
    assert bus.subscriber_count() == 1
    resp.close()
    assert bus.subscriber_count() == 0


def test_cors_only_for_the_local_ui(env):
    client, _, _, _, _ = env
    ok = client.get("/state", headers={"Origin": "http://localhost:3001"})
    assert ok.headers["Access-Control-Allow-Origin"] == "http://localhost:3001"
    evil = client.get("/state", headers={"Origin": "https://example.com"})
    assert "Access-Control-Allow-Origin" not in evil.headers
    pre = client.open("/commands", method="OPTIONS", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert pre.status_code in (200, 204)
    assert "POST" in pre.headers["Access-Control-Allow-Methods"]
    assert "Content-Type" in pre.headers["Access-Control-Allow-Headers"]
