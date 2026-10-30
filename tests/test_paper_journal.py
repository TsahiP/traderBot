"""paperbot journal: schema, events, setups, decisions, orders, trades (P&L and R
for both sides), commands, equity, heartbeat, read-only access."""
import sqlite3
import threading

import pytest

from paper.journal import SCHEMA_VERSION, Journal, JournalError

TS = "2026-10-30T10:15:00-04:00"


@pytest.fixture
def j(tmp_path):
    journal = Journal(tmp_path / "paperbot.db")
    yield journal
    journal.close()


def _trade(j, **over):
    args = dict(run_id="swing_sma", symbol="SPY", side="long", qty=10, entry_ts=TS,
                entry_price=100.0, stop=95.0, target=110.0, config_hash="abc")
    args.update(over)
    return j.open_trade(**args)


def test_migrate_is_idempotent_and_uses_wal(tmp_path):
    path = tmp_path / "paperbot.db"
    Journal(path).close()
    again = Journal(path)
    assert again.schema_version() == SCHEMA_VERSION
    assert again.journal_mode() == "wal"
    again.close()


def test_events_get_monotonic_ids_and_page(j):
    first = j.add_event("bot.start", {"pid": 1})
    second = j.add_event("fill", {"qty": 10, "price": 100.5}, run_id="swing_sma", symbol="SPY")
    assert second["id"] == first["id"] + 1
    assert second["payload"] == {"qty": 10, "price": 100.5} and second["symbol"] == "SPY"
    for i in range(5):
        j.add_event("setup.state", {"i": i})
    page = j.events_since(first["id"], limit=3)
    assert [e["id"] for e in page] == [second["id"], second["id"] + 1, second["id"] + 2]
    assert j.events_since(j.last_event_id()) == []
    assert j.last_event_id() == second["id"] + 5


def test_setup_upsert_keeps_one_row_per_run_symbol_day(j):
    j.upsert_setup("day_orb", "TSLA", "2026-10-30", "ARMED", sentence="range forming")
    j.upsert_setup("day_orb", "TSLA", "2026-10-30", "WAITING", side="long", level=362.18,
                   distance_pct=-0.14, sentence="$0.50 under 362.18", detail={"or_low": 354.47})
    j.upsert_setup("day_orb", "AMD", "2026-10-30", "ARMED")
    rows = j.setups("2026-10-30")
    assert len(rows) == 2
    tsla = j.get_setup("day_orb", "TSLA", "2026-10-30")
    assert tsla["state"] == "WAITING" and tsla["level"] == 362.18
    assert tsla["detail"] == {"or_low": 354.47}
    assert j.get_setup("day_orb", "TSLA", "2026-10-31") is None


def test_decisions_and_entries_today(j):
    stages = [{"stage": "risk", "status": "pass"}]
    entered = j.add_decision(ts=TS, run_id="swing_sma", symbol="SPY", side="long", outcome="entered",
                             qty=10, max_loss=50.0, stages=stages, plan={"stop": 95.0},
                             llm_mode="shadow", llm_verdict="approve", llm_would_block=False,
                             config_hash="abc")
    j.add_decision(ts=TS, run_id="swing_sma", symbol="QQQ", side="long", outcome="blocked",
                   failed_stage="risk", stages=stages, config_hash="abc")
    proposed = j.add_decision(ts=TS, run_id="swing_sma", symbol="AAPL", side="short", outcome="proposed",
                              qty=5, max_loss=40.0, stages=stages, config_hash="abc")
    j.add_decision(ts="2026-10-29T10:00:00-04:00", run_id="swing_sma", symbol="MSFT", side="long",
                   outcome="entered", qty=1, max_loss=9.0, stages=[], config_hash="abc")
    assert j.entries_today("2026-10-30") == [{"symbol": "SPY", "max_loss": 50.0}]
    j.set_decision_outcome(proposed, "entered")
    assert {e["symbol"] for e in j.entries_today("2026-10-30")} == {"SPY", "AAPL"}
    row = j.decision(entered)
    assert row["stages"] == stages and row["plan"] == {"stop": 95.0}
    assert row["llm_would_block"] is False
    assert [d["symbol"] for d in j.decisions(day="2026-10-30")] == ["AAPL", "QQQ", "SPY"]


def test_order_upsert_merges_fields(j):
    j.upsert_order("o1", kind="entry", side="buy", qty=10, type="market", tif="gtc",
                   status="new", submitted_at=TS, decision_id=7)
    j.upsert_order("o1", status="filled", filled_qty=10, filled_avg=100.2)
    row = j.order("o1")
    assert row["status"] == "filled" and row["kind"] == "entry" and row["decision_id"] == 7
    assert row["filled_avg"] == 100.2
    j.upsert_order("o2", parent_id="o1", kind="stop", side="sell", qty=10, status="held", stop_price=95.0)
    assert [o["alpaca_id"] for o in j.orders(statuses=("held", "new"))] == ["o2"]
    with pytest.raises(JournalError):
        j.upsert_order("o3", colour="red")


def test_long_trade_pnl_and_r(j):
    tid = _trade(j)
    assert [t["id"] for t in j.open_trades()] == [tid]
    closed = j.close_trade(tid, exit_ts=TS, exit_price=110.0, exit_reason="target")
    assert closed["pnl"] == 100.0 and closed["r"] == 2.0
    assert j.open_trades() == []
    with pytest.raises(JournalError):
        j.close_trade(tid, exit_ts=TS, exit_price=111.0, exit_reason="target")


def test_short_trade_pnl_and_r(j):
    tid = _trade(j, side="short", stop=105.0, target=90.0)
    closed = j.close_trade(tid, exit_ts=TS, exit_price=105.0, exit_reason="stop")
    assert closed["pnl"] == -50.0 and closed["r"] == -1.0
    win = j.close_trade(_trade(j, side="short", stop=105.0, target=90.0),
                        exit_ts=TS, exit_price=92.5, exit_reason="manual")
    assert win["pnl"] == 75.0 and win["r"] == 1.5


def test_trade_without_a_stop_has_no_r_and_updates_merge(j):
    tid = _trade(j, stop=None, target=None, adopted=True)
    j.update_trade(tid, stop=96.0, mae_r=-0.4)
    row = j.trade(tid)
    assert row["stop"] == 96.0 and row["adopted"] is True and row["mae_r"] == -0.4
    closed = j.close_trade(_trade(j, stop=None, target=None), exit_ts=TS, exit_price=101.0,
                           exit_reason="broker")
    assert closed["pnl"] == 10.0 and closed["r"] is None


def test_trades_filters(j):
    a = _trade(j)
    _trade(j, run_id="day_orb", symbol="TSLA")
    j.close_trade(a, exit_ts=TS, exit_price=105.0, exit_reason="signal")
    assert [t["id"] for t in j.trades(closed=True)] == [a]
    assert [t["symbol"] for t in j.trades(run_id="day_orb")] == ["TSLA"]


def test_commands_round_trip(j):
    cid = j.add_command("ui", "close_position", {"symbol": "SPY"}, ts=TS)
    assert j.command(cid)["status"] == "queued"
    j.finish_command(cid, "done", {"order_id": "o9"})
    row = j.command(cid)
    assert row["status"] == "done" and row["args"] == {"symbol": "SPY"} and row["result"] == {"order_id": "o9"}


def test_equity_snapshots(j):
    j.add_equity("2026-10-30T10:00:00-04:00", 100_000.0, 90_000.0, 12.5)
    j.add_equity("2026-10-30T10:01:00-04:00", 100_010.0, 90_000.0, 22.5)
    j.add_equity("2026-10-30T10:01:00-04:00", 100_011.0, 90_000.0, 23.5)  # same minute replaces
    rows = j.equity(since="2026-10-30T10:00:30-04:00")
    assert rows == [{"ts": "2026-10-30T10:01:00-04:00", "equity": 100_011.0, "cash": 90_000.0,
                     "open_pnl": 23.5}]


def test_heartbeat_is_a_single_row(j):
    assert j.read_heartbeat() is None
    j.write_heartbeat(ts=TS, pid=10, paused=False, market="open", next_wake=TS,
                      streams={"trading": "up"}, config_hash="abc")
    j.write_heartbeat(ts=TS, pid=10, paused=True, market="open", next_wake=TS,
                      streams={"trading": "down"}, config_hash="abc")
    hb = j.read_heartbeat()
    assert hb["paused"] is True and hb["streams"] == {"trading": "down"}


def test_prune_drops_old_setup_state_events_only(j):
    j.add_event("setup.state", {}, ts="2026-09-01T10:00:00-04:00")
    j.add_event("fill", {}, ts="2026-09-01T10:00:00-04:00")
    j.add_event("setup.state", {}, ts="2026-10-30T10:00:00-04:00")
    assert j.prune_setup_events(before="2026-10-01") == 1
    assert [e["type"] for e in j.events_since(0)] == ["fill", "setup.state"]


def test_readonly_reads_but_cannot_write(tmp_path):
    path = tmp_path / "paperbot.db"
    writer = Journal(path)
    writer.add_event("bot.start", {})
    reader = Journal(path, readonly=True)
    assert len(reader.events_since(0)) == 1
    with pytest.raises(sqlite3.OperationalError):
        reader.add_event("bot.stop", {})
    reader.close()
    writer.close()


def test_readonly_on_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        Journal(tmp_path / "nope.db", readonly=True)


def test_writes_from_several_threads(j):
    def worker():
        for _ in range(50):
            j.add_event("quote", {})

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [e["id"] for e in j.events_since(0, limit=1000)]
    assert len(ids) == 200 and len(set(ids)) == 200
