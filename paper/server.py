"""paperbot HTTP server (127.0.0.1 only): the Paper desk UI's live link to the bot.

GET  /health     {ok, pid, uptime_s, subscribers}
GET  /state      the snapshot the bot loop builds every tick (never touches Alpaca)
GET  /stream     Server-Sent Events. Each frame is `id: <event id>` (stored
                 events only) + `data: <event JSON>`; there is no `event:`
                 field, so one `onmessage` handler gets everything and reads
                 `type` from the JSON. `Last-Event-ID` (browser reconnect) or
                 `?after=<id>` replays what was missed from the journal before
                 going live, without duplicates. `: ping` every 15 s.
POST /commands   {kind, args}: validated, written to the journal's commands
                 table, handed to the bot loop; 202 {command_id, status:
                 "queued"}. The outcome arrives later as a `command` event.

CORS is open to the local Next.js UI only (localhost / 127.0.0.1 on 3000-3001).
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time

from flask import Flask, Response, jsonify, request, stream_with_context

from market_symbols import is_valid_symbol, normalize_symbol
from paper.config import APPROVAL_MODES

logger = logging.getLogger("paperbot")

PING_S = 15.0
REPLAY_PAGE = 500
ALLOWED_ORIGINS = {f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (3000, 3001)}
# kind -> allowed args
COMMAND_ARGS = {
    "pause": (), "resume": (), "flatten_all": (),
    "close_position": ("symbol",), "set_approval": ("mode",),
    "approve": ("proposal_id",), "reject": ("proposal_id",),
}


class BadCommand(ValueError):
    pass


def sse_frame(event: dict) -> str:
    head = f"id: {event['id']}\n" if event.get("id") is not None else ""
    return f"{head}data: {json.dumps(event, default=str)}\n\n"


def validate_command(body) -> tuple[str, dict]:
    if not isinstance(body, dict):
        raise BadCommand("body must be a JSON object")
    kind = body.get("kind")
    if kind not in COMMAND_ARGS:
        raise BadCommand(f"unknown kind {kind!r}; use one of {', '.join(COMMAND_ARGS)}")
    args = body.get("args") or {}
    if not isinstance(args, dict):
        raise BadCommand("args must be an object")
    extra = sorted(set(args) - set(COMMAND_ARGS[kind]))
    if extra:
        raise BadCommand(f"{kind} takes no {', '.join(extra)}")
    out = {}
    if "symbol" in COMMAND_ARGS[kind]:
        sym = args.get("symbol")
        if not isinstance(sym, str) or not is_valid_symbol(sym.strip()):
            raise BadCommand(f"{kind} needs a valid symbol")
        out["symbol"] = normalize_symbol(sym.strip())
    if "mode" in COMMAND_ARGS[kind]:
        if args.get("mode") not in APPROVAL_MODES:
            raise BadCommand(f"mode must be one of {', '.join(APPROVAL_MODES)}")
        out["mode"] = args["mode"]
    if "proposal_id" in COMMAND_ARGS[kind]:
        try:
            out["proposal_id"] = int(args.get("proposal_id"))
        except (TypeError, ValueError):
            raise BadCommand(f"{kind} needs a numeric proposal_id") from None
    return kind, out


def create_app(bot, ping_s: float = PING_S) -> Flask:
    """`bot` provides: snapshot (dict), pid, post(item), journal, bus."""
    app = Flask("paperbot")
    started = time.monotonic()

    @app.after_request
    def cors(resp):
        origin = request.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Last-Event-ID"
        return resp

    @app.get("/health")
    def health():
        return jsonify(ok=True, pid=bot.pid, uptime_s=round(time.monotonic() - started, 1),
                       subscribers=bot.bus.subscriber_count())

    @app.get("/state")
    def state():
        return jsonify(bot.snapshot or {"starting": True})

    @app.post("/commands")
    def commands():
        try:
            kind, args = validate_command(request.get_json(silent=True))
        except BadCommand as exc:
            return jsonify(error=str(exc)), 400
        cid = bot.journal.add_command("ui", kind, args)
        bot.post({"kind": "command", "id": cid, "cmd": kind, "args": args})
        return jsonify(command_id=cid, status="queued"), 202

    @app.get("/stream")
    def stream():
        raw_after = request.headers.get("Last-Event-ID") or request.args.get("after")
        try:
            after = int(raw_after) if raw_after not in (None, "") else None
        except ValueError:
            after = None
        q = bot.bus.subscribe()

        def generate():
            last = after if after is not None else bot.journal.last_event_id()
            try:
                yield ": connected\n\n"
                if after is not None:
                    while True:
                        page = bot.bus.replay(last, limit=REPLAY_PAGE)
                        for event in page:
                            last = event["id"]
                            yield sse_frame(event)
                        if len(page) < REPLAY_PAGE:
                            break
                while bot.bus.is_subscribed(q):
                    try:
                        event = q.get(timeout=ping_s)
                    except queue.Empty:
                        yield ": ping\n\n"
                        continue
                    if event.get("id") is not None and event["id"] <= last:
                        continue                    # already sent by the replay
                    if event.get("id") is not None:
                        last = event["id"]
                    yield sse_frame(event)
            finally:
                bot.bus.unsubscribe(q)

        return Response(stream_with_context(generate()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def start_server(bot, port: int, host: str = "127.0.0.1") -> threading.Thread:
    """Serve on a daemon thread (Werkzeug, threaded: one thread per SSE client)."""
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app = create_app(bot)
    thread = threading.Thread(target=app.run, name="paperbot-http", daemon=True,
                              kwargs={"host": host, "port": port, "threaded": True, "use_reloader": False})
    thread.start()
    logger.info("paperbot HTTP on http://%s:%s (SSE /stream, /state, /commands)", host, port)
    return thread
