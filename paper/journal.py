"""paperbot journal: one SQLite file (`output/paperbot.db`) holding everything
the bot did and saw, so the UI, the statistics and a restart all read one truth.

WAL mode lets dashboard.py read (``readonly=True``, ``mode=ro``) while the bot
writes, without the PermissionError a replaced JSON file gave on Windows.
The bot is the only writer; a lock serialises the threads inside it (main
loop, HTTP commands, streams) on the one shared connection.

Tables (spec §6): events (append-only, ``id`` is the SSE id), setups (current
state per run/symbol/day), decisions (the funnel of every trigger), orders,
trades (P&L and R, both sides), commands (UI audit trail), equity (minute
snapshots) and a single-row heartbeat. JSON columns are decoded on read;
flag columns come back as bools.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import risk

NY = ZoneInfo("America/New_York")
SCHEMA_VERSION = 1
OUTCOMES = ("entered", "blocked", "vetoed", "expired", "rejected", "proposed")

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, type TEXT NOT NULL,
    run_id TEXT, symbol TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS events_type_ts ON events(type, ts);
CREATE TABLE IF NOT EXISTS setups (
    run_id TEXT NOT NULL, symbol TEXT NOT NULL, day TEXT NOT NULL, state TEXT NOT NULL,
    side TEXT, level REAL, distance_pct REAL, sentence TEXT, detail TEXT, updated_at TEXT,
    PRIMARY KEY (run_id, symbol, day));
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, run_id TEXT, symbol TEXT, side TEXT,
    outcome TEXT NOT NULL, failed_stage TEXT, score REAL, qty REAL, max_loss REAL,
    stages TEXT, plan TEXT, llm_mode TEXT, llm_verdict TEXT, llm_would_block INTEGER,
    config_hash TEXT);
CREATE INDEX IF NOT EXISTS decisions_ts ON decisions(ts);
CREATE TABLE IF NOT EXISTS orders (
    alpaca_id TEXT PRIMARY KEY, parent_id TEXT, decision_id INTEGER, trade_id INTEGER,
    kind TEXT, side TEXT, symbol TEXT, qty REAL, type TEXT, tif TEXT, status TEXT,
    limit_price REAL, stop_price REAL, filled_qty REAL, filled_avg REAL,
    submitted_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id INTEGER, run_id TEXT, symbol TEXT,
    side TEXT, qty REAL, entry_ts TEXT, entry_price REAL, stop REAL, target REAL,
    exit_ts TEXT, exit_price REAL, exit_reason TEXT, pnl REAL, r REAL, mae_r REAL, mfe_r REAL,
    score REAL, llm_mode TEXT, llm_verdict TEXT, adopted INTEGER, flat_by TEXT, config_hash TEXT);
CREATE INDEX IF NOT EXISTS trades_exit_ts ON trades(exit_ts);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, source TEXT, kind TEXT,
    args TEXT, status TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS equity (
    ts TEXT PRIMARY KEY, equity REAL, cash REAL, open_pnl REAL);
CREATE TABLE IF NOT EXISTS heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1), ts TEXT, pid INTEGER, paused INTEGER,
    market TEXT, next_wake TEXT, streams TEXT, config_hash TEXT);
"""

_JSON = {"payload", "detail", "stages", "plan", "args", "result", "streams"}
_BOOL = {"llm_would_block", "adopted", "paused"}
_ORDER_COLS = ("parent_id", "decision_id", "trade_id", "kind", "side", "symbol", "qty", "type",
               "tif", "status", "limit_price", "stop_price", "filled_qty", "filled_avg",
               "submitted_at", "updated_at")
_TRADE_UPDATABLE = ("qty", "stop", "target", "mae_r", "mfe_r", "score", "llm_verdict", "flat_by")
_DECISION_UPDATABLE = ("outcome", "failed_stage", "score", "qty", "max_loss", "stages", "plan",
                       "llm_verdict", "llm_would_block")


class JournalError(ValueError):
    """Bad input to a journal call (unknown column, closing a closed trade...)."""


def now_iso(timespec: str = "seconds") -> str:
    return datetime.now(NY).isoformat(timespec=timespec)


def _enc(col: str, value):
    if col in _JSON and value is not None:
        return json.dumps(value)
    if col in _BOOL and value is not None:
        return int(bool(value))
    return value


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = {}
    for key in row.keys():
        value = row[key]
        if key in _JSON and value is not None:
            value = json.loads(value)
        elif key in _BOOL and value is not None:
            value = bool(value)
        out[key] = value
    return out


def trade_result(side: str, qty: float, entry: float, stop: float | None,
                 exit_price: float) -> tuple[float, float | None]:
    """(P&L in dollars, R multiple or None without a usable stop)."""
    per_share = exit_price - entry if side == "long" else entry - exit_price
    pnl = round(per_share * qty, 2)
    if stop is None:
        return pnl, None
    at_risk = risk.risk_per_share(entry, stop, side)
    return pnl, (round(per_share / at_risk, 3) if at_risk > 0 else None)


class Journal:
    def __init__(self, path: Path, readonly: bool = False):
        path = Path(path)
        self.readonly = readonly
        if readonly:
            if not path.exists():
                raise FileNotFoundError(path)
            self._conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True,
                                         check_same_thread=False, timeout=5.0)
        else:
            self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        if not readonly:
            self.migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ plumbing

    def _write(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def _all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [_row(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params=()) -> dict | None:
        with self._lock:
            return _row(self._conn.execute(sql, params).fetchone())

    def migrate(self) -> None:
        with self._lock, self._conn:
            if self.schema_version() < 1:
                self._conn.executescript(_SCHEMA_V1)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def schema_version(self) -> int:
        with self._lock:
            return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def journal_mode(self) -> str:
        with self._lock:
            return self._conn.execute("PRAGMA journal_mode").fetchone()[0]

    # ------------------------------------------------------------ events

    def add_event(self, type: str, payload: dict | None = None, run_id: str | None = None,
                  symbol: str | None = None, ts: str | None = None) -> dict:
        ts = ts or now_iso("milliseconds")
        cur = self._write("INSERT INTO events (ts, type, run_id, symbol, payload) VALUES (?, ?, ?, ?, ?)",
                          (ts, type, run_id, symbol, _enc("payload", payload or {})))
        return {"id": cur.lastrowid, "ts": ts, "type": type, "run_id": run_id, "symbol": symbol,
                "payload": payload or {}}

    def events_since(self, after_id: int, limit: int = 500) -> list[dict]:
        return self._all("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit))

    def last_event_id(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]

    def prune_setup_events(self, before: str) -> int:
        """Drop `setup.state` events older than `before` (ISO date or timestamp)."""
        return self._write("DELETE FROM events WHERE type = 'setup.state' AND ts < ?", (before,)).rowcount

    # ------------------------------------------------------------ setups

    def upsert_setup(self, run_id: str, symbol: str, day: str, state: str, side: str | None = None,
                     level: float | None = None, distance_pct: float | None = None,
                     sentence: str | None = None, detail: dict | None = None,
                     ts: str | None = None) -> None:
        self._write(
            "INSERT INTO setups (run_id, symbol, day, state, side, level, distance_pct, sentence, detail, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (run_id, symbol, day) DO UPDATE SET state = excluded.state, side = excluded.side,"
            " level = excluded.level, distance_pct = excluded.distance_pct, sentence = excluded.sentence,"
            " detail = excluded.detail, updated_at = excluded.updated_at",
            (run_id, symbol, day, state, side, level, distance_pct, sentence,
             _enc("detail", detail), ts or now_iso()),
        )

    def get_setup(self, run_id: str, symbol: str, day: str) -> dict | None:
        return self._one("SELECT * FROM setups WHERE run_id = ? AND symbol = ? AND day = ?",
                         (run_id, symbol, day))

    def setups(self, day: str) -> list[dict]:
        return self._all("SELECT * FROM setups WHERE day = ? ORDER BY run_id, symbol", (day,))

    # ------------------------------------------------------------ decisions

    def add_decision(self, *, ts: str, run_id: str, symbol: str, side: str, outcome: str,
                     stages: list, config_hash: str, failed_stage: str | None = None,
                     score: float | None = None, qty: float | None = None,
                     max_loss: float | None = None, plan: dict | None = None,
                     llm_mode: str | None = None, llm_verdict: str | None = None,
                     llm_would_block: bool | None = None) -> int:
        if outcome not in OUTCOMES:
            raise JournalError(f"unknown decision outcome {outcome!r}")
        cur = self._write(
            "INSERT INTO decisions (ts, run_id, symbol, side, outcome, failed_stage, score, qty, max_loss,"
            " stages, plan, llm_mode, llm_verdict, llm_would_block, config_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, run_id, symbol, side, outcome, failed_stage, score, qty, max_loss,
             _enc("stages", stages), _enc("plan", plan), llm_mode, llm_verdict,
             _enc("llm_would_block", llm_would_block), config_hash),
        )
        return cur.lastrowid

    def set_decision_outcome(self, decision_id: int, outcome: str, failed_stage: str | None = None) -> None:
        if outcome not in OUTCOMES:
            raise JournalError(f"unknown decision outcome {outcome!r}")
        self._write("UPDATE decisions SET outcome = ?, failed_stage = ? WHERE id = ?",
                    (outcome, failed_stage, decision_id))

    def update_decision(self, decision_id: int, **fields) -> None:
        """Rewrite a decision after a re-check (an approved proposal, a queued entry)."""
        unknown = sorted(set(fields) - set(_DECISION_UPDATABLE))
        if unknown:
            raise JournalError(f"decision field(s) {', '.join(unknown)} cannot be updated")
        if "outcome" in fields and fields["outcome"] not in OUTCOMES:
            raise JournalError(f"unknown decision outcome {fields['outcome']!r}")
        if fields:
            sets = ", ".join(f"{c} = ?" for c in fields)
            self._write(f"UPDATE decisions SET {sets} WHERE id = ?",
                        (*(_enc(c, v) for c, v in fields.items()), decision_id))

    def decisions_by_outcome(self, outcome: str) -> list[dict]:
        return self._all("SELECT * FROM decisions WHERE outcome = ? ORDER BY id", (outcome,))

    def decision(self, decision_id: int) -> dict | None:
        return self._one("SELECT * FROM decisions WHERE id = ?", (decision_id,))

    def decisions(self, day: str | None = None, limit: int = 200) -> list[dict]:
        if day:
            return self._all("SELECT * FROM decisions WHERE substr(ts, 1, 10) = ? ORDER BY id DESC LIMIT ?",
                             (day, limit))
        return self._all("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))

    def entries_today(self, day: str) -> list[dict]:
        """Rows for `risk.check_entry`: every entry placed on `day` with its max loss."""
        return self._all("SELECT symbol, max_loss FROM decisions WHERE outcome = 'entered'"
                         " AND substr(ts, 1, 10) = ? ORDER BY id", (day,))

    # ------------------------------------------------------------ orders

    def upsert_order(self, alpaca_id: str, **fields) -> None:
        unknown = sorted(set(fields) - set(_ORDER_COLS))
        if unknown:
            raise JournalError(f"unknown order field(s) {', '.join(unknown)}")
        fields.setdefault("updated_at", now_iso())
        cols = list(fields)
        self._write(
            f"INSERT INTO orders (alpaca_id, {', '.join(cols)}) VALUES (?{', ?' * len(cols)})"
            f" ON CONFLICT (alpaca_id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols)}",
            (alpaca_id, *fields.values()),
        )

    def order(self, alpaca_id: str) -> dict | None:
        return self._one("SELECT * FROM orders WHERE alpaca_id = ?", (alpaca_id,))

    def orders(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        if statuses:
            marks = ", ".join("?" * len(statuses))
            return self._all(f"SELECT * FROM orders WHERE status IN ({marks}) ORDER BY submitted_at, alpaca_id",
                             tuple(statuses))
        return self._all("SELECT * FROM orders ORDER BY submitted_at, alpaca_id")

    def orders_matching(self, *, symbol: str | None = None, decision_id: int | None = None,
                        trade_id: int | None = None, since: str | None = None) -> list[dict]:
        """Orders by any of: symbol (submitted at/after `since`), decision, trade."""
        where, params = [], []
        if symbol:
            clause = "symbol = ?"
            params.append(symbol)
            if since:
                clause += " AND submitted_at >= ?"
                params.append(since)
            where.append(f"({clause})")
        if decision_id is not None:
            where.append("decision_id = ?")
            params.append(decision_id)
        if trade_id is not None:
            where.append("trade_id = ?")
            params.append(trade_id)
        if not where:
            return []
        return self._all(f"SELECT * FROM orders WHERE {' OR '.join(where)} ORDER BY submitted_at, alpaca_id",
                         tuple(params))

    # ------------------------------------------------------------ trades

    def open_trade(self, *, run_id: str, symbol: str, side: str, qty: float, entry_ts: str,
                   entry_price: float, stop: float | None, target: float | None,
                   config_hash: str | None = None, decision_id: int | None = None,
                   score: float | None = None, llm_mode: str | None = None,
                   llm_verdict: str | None = None, adopted: bool = False,
                   flat_by: str | None = None) -> int:
        """`flat_by` (HH:MM NY) = exit at that time today, win or lose (day trades)."""
        cur = self._write(
            "INSERT INTO trades (decision_id, run_id, symbol, side, qty, entry_ts, entry_price, stop, target,"
            " score, llm_mode, llm_verdict, adopted, flat_by, config_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (decision_id, run_id, symbol, side, qty, entry_ts, entry_price, stop, target, score,
             llm_mode, llm_verdict, int(adopted), flat_by, config_hash),
        )
        return cur.lastrowid

    def update_trade(self, trade_id: int, **fields) -> None:
        unknown = sorted(set(fields) - set(_TRADE_UPDATABLE))
        if unknown:
            raise JournalError(f"trade field(s) {', '.join(unknown)} cannot be updated")
        if fields:
            sets = ", ".join(f"{c} = ?" for c in fields)
            self._write(f"UPDATE trades SET {sets} WHERE id = ?", (*fields.values(), trade_id))

    def close_trade(self, trade_id: int, *, exit_ts: str, exit_price: float, exit_reason: str) -> dict:
        with self._lock:
            trade = self.trade(trade_id)
            if trade is None:
                raise JournalError(f"no trade {trade_id}")
            if trade["exit_ts"] is not None:
                raise JournalError(f"trade {trade_id} is already closed")
            pnl, r = trade_result(trade["side"], trade["qty"], trade["entry_price"], trade["stop"], exit_price)
            self._write("UPDATE trades SET exit_ts = ?, exit_price = ?, exit_reason = ?, pnl = ?, r = ?"
                        " WHERE id = ?", (exit_ts, exit_price, exit_reason, pnl, r, trade_id))
            return self.trade(trade_id)

    def trade(self, trade_id: int) -> dict | None:
        return self._one("SELECT * FROM trades WHERE id = ?", (trade_id,))

    def open_trades(self) -> list[dict]:
        return self._all("SELECT * FROM trades WHERE exit_ts IS NULL ORDER BY id")

    def trades(self, run_id: str | None = None, closed: bool | None = None,
               since: str | None = None, limit: int = 1000) -> list[dict]:
        where, params = [], []
        if run_id:
            where.append("run_id = ?")
            params.append(run_id)
        if closed is True:
            where.append("exit_ts IS NOT NULL")
        elif closed is False:
            where.append("exit_ts IS NULL")
        if since:
            where.append("entry_ts >= ?")
            params.append(since)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        return self._all(f"SELECT * FROM trades{clause} ORDER BY id LIMIT ?", (*params, limit))

    # ------------------------------------------------------------ commands

    def add_command(self, source: str, kind: str, args: dict | None = None, ts: str | None = None) -> int:
        cur = self._write("INSERT INTO commands (ts, source, kind, args, status) VALUES (?, ?, ?, ?, 'queued')",
                          (ts or now_iso(), source, kind, _enc("args", args or {})))
        return cur.lastrowid

    def finish_command(self, command_id: int, status: str, result: dict | None = None) -> None:
        self._write("UPDATE commands SET status = ?, result = ? WHERE id = ?",
                    (status, _enc("result", result), command_id))

    def command(self, command_id: int) -> dict | None:
        return self._one("SELECT * FROM commands WHERE id = ?", (command_id,))

    # ------------------------------------------------------------ equity + heartbeat

    def add_equity(self, ts: str, equity: float, cash: float, open_pnl: float) -> None:
        self._write("INSERT OR REPLACE INTO equity (ts, equity, cash, open_pnl) VALUES (?, ?, ?, ?)",
                    (ts, equity, cash, open_pnl))

    def equity(self, since: str | None = None) -> list[dict]:
        if since:
            return self._all("SELECT * FROM equity WHERE ts >= ? ORDER BY ts", (since,))
        return self._all("SELECT * FROM equity ORDER BY ts")

    def write_heartbeat(self, *, ts: str, pid: int, paused: bool, market: str,
                        next_wake: str | None, streams: dict, config_hash: str) -> None:
        self._write("INSERT OR REPLACE INTO heartbeat (id, ts, pid, paused, market, next_wake, streams,"
                    " config_hash) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                    (ts, pid, int(paused), market, next_wake, _enc("streams", streams), config_hash))

    def read_heartbeat(self) -> dict | None:
        row = self._one("SELECT * FROM heartbeat WHERE id = 1")
        if row:
            row.pop("id")
        return row
