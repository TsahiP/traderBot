"""dashboard.py's read-only paper endpoints: history from paperbot.db when the
bot is down, an online flag from the heartbeat, never a 500 on a missing DB."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import dashboard
from paper.journal import Journal

NY = ZoneInfo("America/New_York")


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "paperbot.db"
    monkeypatch.setattr(dashboard, "PAPER_DB_PATH", db)
    monkeypatch.setattr(dashboard, "TRADES_FILE", tmp_path / "none_live.csv")
    monkeypatch.setattr(dashboard, "TRADES_BT_FILE", tmp_path / "none_bt.csv")
    yield dashboard.app.test_client(), db


def _seed(db, heartbeat_age_s=2.0):
    j = Journal(db)
    now = datetime.now(NY)
    j.write_heartbeat(ts=(now - timedelta(seconds=heartbeat_age_s)).isoformat(timespec="seconds"), pid=7,
                      paused=False, market="open", next_wake=None, streams={"trading": "up"},
                      config_hash="abc")
    day = now.date().isoformat()
    j.add_decision(ts=f"{day}T09:45:00-04:00", run_id="swing_sma", symbol="SPY", side="long",
                   outcome="entered", qty=50, max_loss=200.0, stages=[], config_hash="abc")
    j.add_decision(ts=f"{day}T09:46:00-04:00", run_id="swing_sma", symbol="QQQ", side="long",
                   outcome="proposed", qty=5, max_loss=40.0, stages=[], plan={"stop": 1.0}, config_hash="abc")
    j.upsert_setup("swing_sma", "SPY", day, "MANAGING", sentence="holding long")
    closed = j.open_trade(run_id="swing_sma", symbol="SPY", side="long", qty=50, entry_ts="2026-10-28T09:45:00-04:00",
                          entry_price=100.0, stop=96.0, target=108.0)
    j.close_trade(closed, exit_ts="2026-10-29T10:00:00-04:00", exit_price=108.0, exit_reason="target")
    j.open_trade(run_id="adopted", symbol="NVDA", side="short", qty=5, entry_ts="2026-10-30T09:31:00-04:00",
                 entry_price=180.0, stop=184.0, target=172.0, adopted=True)
    j.add_event("bot.start", {"pid": 7})
    j.add_event("fill", {"qty": 50})
    j.add_equity("2026-10-30T10:00:00-04:00", 100_000.0, 90_000.0, 12.0)
    j.close()


def test_missing_db_is_empty_not_an_error(env):
    client, _ = env
    for path in ("/api/paper/state", "/api/paper/trades", "/api/paper/events", "/api/paper/equity",
                 "/api/paper/decisions"):
        resp = client.get(path)
        assert resp.status_code == 200, path
    assert client.get("/api/paper/state").get_json() == {"online": False, "empty": True}
    assert client.get("/api/paper/trades").get_json() == {"trades": []}


def test_state_from_the_journal(env):
    client, db = env
    _seed(db)
    s = client.get("/api/paper/state").get_json()
    assert s["online"] is True and s["empty"] is False
    assert s["heartbeat"]["pid"] == 7 and s["heartbeat"]["streams"] == {"trading": "up"}
    assert [t["symbol"] for t in s["open_trades"]] == ["NVDA"]
    assert [p["symbol"] for p in s["proposals"]] == ["QQQ"]
    assert s["counters"] == {"entries_today": 1, "daily_loss_used": 200.0}
    assert [x["symbol"] for x in s["setups"]] == ["SPY"]
    assert s["last_event_id"] == 2


def test_a_stale_heartbeat_is_offline(env):
    client, db = env
    _seed(db, heartbeat_age_s=60)
    assert client.get("/api/paper/state").get_json()["online"] is False


def test_trades_events_equity_decisions(env):
    client, db = env
    _seed(db)
    trades = client.get("/api/paper/trades").get_json()["trades"]
    assert len(trades) == 1 and trades[0]["r"] == 2.0 and trades[0]["exit_reason"] == "target"
    assert client.get("/api/paper/trades?run_id=day_orb").get_json()["trades"] == []
    events = client.get("/api/paper/events?after=1").get_json()["events"]
    assert [e["type"] for e in events] == ["fill"]
    eq = client.get("/api/paper/equity").get_json()["equity"]
    assert eq == [{"ts": "2026-10-30T10:00:00-04:00", "equity": 100_000.0, "cash": 90_000.0, "open_pnl": 12.0}]
    decisions = client.get("/api/paper/decisions").get_json()["decisions"]
    assert [d["symbol"] for d in decisions] == ["QQQ", "SPY"]
    assert client.get("/api/paper/events?after=x").status_code == 400


def test_the_ledger_lists_paper_trades(env):
    client, db = env
    _seed(db)
    trades = client.get("/api/trades").get_json()["trades"]
    assert [(t["source"], t["qty"], t["pnl"]) for t in trades] == [("paper", 50, 400.0)]
    assert trades[0]["entry_date"] == "2026-10-28 09:45"
