"""Day-skill board: opening-range rows, sized like the skill, no paperbot config changes."""
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

import skill_board
from market_calendar import NY


def _five_min(day, closes):
    start = datetime.combine(day, time(9, 30), NY)
    idx = [start + timedelta(minutes=5 * i) for i in range(len(closes))]
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c + 0.2, "Low": c - 0.2, "Close": c, "Volume": 1e4},
        index=pd.DatetimeIndex(idx),
    )


def test_stop_is_pulled_under_the_live_price():
    # The rejection: midpoint at or above Alpaca's base price 283.18.
    entry, stop, target = skill_board.long_bracket(284.00, 283.18)
    assert entry == 283.18
    assert stop <= 283.17
    assert target == round(entry + 2 * (entry - stop), 2)
    # A midpoint already under the quote stays the midpoint. Target is still 2R from the live price.
    entry, stop, target = skill_board.long_bracket(280.00, 283.18)
    assert stop == 280.00
    assert target == round(283.18 + 2 * (283.18 - 280.00), 2)


def test_triggered_row_passes_and_sizes(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_board, "ORDERS_PATH", tmp_path / "skill_orders.json")
    day = datetime(2026, 10, 27).date()
    df = _five_min(day, [100, 100.5, 100.3, 100.4, 100.9, 101.2, 101.0])
    now = datetime.combine(day, time(10, 15), NY)
    account = {"equity": 100_000.0, "cash": 100_000.0, "buying_power": 100_000.0}
    row = skill_board.evaluate_symbol("TSLA", df, now, account, [])
    assert row["status"] == "triggered"
    assert row["verdict"] == "PASS"
    assert row["stop"] < row["entry"] < row["target"]
    assert row["qty"] > 0
    assert row["max_loss"] <= skill_board.MAX_LOSS


def test_send_passes_without_a_confirm(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_board, "ORDERS_PATH", tmp_path / "skill_orders.json")
    monkeypatch.setattr(skill_board.scanner, "watchlist_symbols", lambda: ["TSLA"])
    day = datetime(2026, 10, 27).date()
    df = _five_min(day, [100, 100.5, 100.3, 100.4, 100.9, 101.2, 101.0])
    now = datetime.combine(day, time(10, 15), NY)
    sent_orders = []

    class _Broker:
        def account(self):
            return {"equity": 100_000.0, "cash": 100_000.0, "buying_power": 100_000.0}

        def latest_price(self, symbol):
            return 101.0

        def submit_bracket(self, symbol, side, qty, stop, target, tif="day", client_order_id=None):
            sent_orders.append({"symbol": symbol, "stop": stop, "target": target})
            assert stop <= 100.99
            assert target == round(101.0 + 2 * (101.0 - stop), 2)
            return {"id": "ord-1"}

    names = [{"symbol": "TSLA", "setups": ["breakout_20d"]}]
    sent = skill_board.send_passes(
        _Broker(), now=now, fetch=lambda symbols, **kw: {"TSLA": df}, universe=names)
    assert [s["symbol"] for s in sent] == ["TSLA"]
    assert sent_orders[0]["symbol"] == "TSLA"
    assert sent[0]["stop"] < sent[0]["entry"] <= 101.0
    again = skill_board.send_passes(
        _Broker(), now=now, fetch=lambda symbols, **kw: {"TSLA": df}, universe=names)
    assert again == []


def test_waiting_has_no_order(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_board, "ORDERS_PATH", tmp_path / "skill_orders.json")
    day = datetime(2026, 10, 27).date()
    df = _five_min(day, [100, 100.5, 100.3, 100.4])
    now = datetime.combine(day, time(9, 52), NY)
    row = skill_board.evaluate_symbol("TSLA", df, now, None, [])
    assert row["status"] == "waiting"
    assert row["verdict"] is None
    assert row["qty"] is None


def test_before_the_open_the_scanner_list_is_not_traded():
    day = datetime(2026, 10, 27).date()
    now = datetime.combine(day, time(9, 20), NY)
    board = skill_board.build_board(
        now, fetch=lambda symbols, **kw: {},
        universe=[{"symbol": "NVDA", "setups": ["bull_flag", "reclaim_ma20"]}],
    )
    assert board["scan"] == "daily"
    assert board["rows"][0]["status"] == "setup"
    assert board["rows"][0]["setups"] == ["bull_flag", "reclaim_ma20"]
    assert board["rows"][0]["verdict"] is None
