"""Scanner: universe filtering, daily setups, ORB rule, scan output."""
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

import tools.scanner as scanner
from market.market_calendar import NY

NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust|Q|N|N|100|Y|N
ZZZT|Test Co - Common Stock|Q|Y|N|100|N|N
ABCDW|ABCD Corp - Warrants|Q|N|N|100|N|N
BADC|Bad Co - Common Stock|Q|N|D|100|N|N
File Creation Time: 1027202608:00|||||||"""
OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Inc. Class B|N|BRK.B|N|100|N|BRK.B
KO|Coca-Cola Company (The) Common Stock|N|KO|N|100|N|KO
JPM$C|JPMorgan Preferred C|N|JPMpC|N|100|N|JPM-C
AAPL|Apple dup|N|AAPL|N|100|N|AAPL
File Creation Time: 1027202608:00|||||||"""


def test_parse_universe_keeps_common_stock_only():
    syms = [r["symbol"] for r in scanner.parse_universe(NASDAQ, OTHER)]
    assert syms == ["AAPL", "BRK-B", "KO"]


def _daily(closes, volume=1e6):
    idx = pd.date_range("2026-07-01", periods=len(closes), freq="B")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 0.5, "Low": c - 0.5, "Close": c, "Volume": volume}, index=idx)


def test_daily_setups():
    assert "breakout_20d" in scanner.daily_setups(_daily(list(np.linspace(100, 105, 30)) + [110]))
    flag = list(np.linspace(80, 85, 10)) + list(np.linspace(85, 100, 10)) + [100, 99, 100.5, 99.5, 100]
    assert "bull_flag" in scanner.daily_setups(_daily(flag))
    reclaim = [100] * 25 + list(np.linspace(99, 95, 5)) + [101]
    assert "reclaim_ma20" in scanner.daily_setups(_daily(reclaim))
    falling = list(np.linspace(100, 70, 30))
    assert "oversold" in scanner.daily_setups(_daily(falling))
    assert "oversold_bounce" in scanner.daily_setups(_daily(falling + [71.0]))
    assert scanner.daily_setups(_daily([100.0] * 30)) == []


def _five_min(day, closes):
    start = datetime.combine(day, time(9, 30), NY)
    idx = [start + timedelta(minutes=5 * i) for i in range(len(closes))]
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 0.2, "Low": c - 0.2, "Close": c, "Volume": 1e4},
                        index=pd.DatetimeIndex(idx))


def test_orb_breakout_first_close_above_range():
    day = datetime(2026, 10, 27).date()
    df = _five_min(day, [100, 100.5, 100.3, 100.4, 100.9, 101.2, 100.0])
    hit = scanner.orb_breakout(df, now=datetime.combine(day, time(11, 0), NY))
    assert hit == {"at": "09:55", "close": 100.9, "range_high": 100.7, "range_low": 99.8}
    # the breakout bar is not complete yet -> no hit
    assert scanner.orb_breakout(df, now=datetime.combine(day, time(9, 52), NY)) is None


def test_run_scan_writes_sorted_results(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "RESULTS_PATH", tmp_path / "scan.json")
    data = {
        "BIG": _daily(list(np.linspace(100, 105, 30)) + [110], volume=5e6),
        "SMALL": _daily(list(np.linspace(100, 105, 30)) + [110], volume=1e6),
        "THIN": _daily(list(np.linspace(100, 105, 30)) + [110], volume=1e3),
        "FLAT": _daily([50.0] * 31),
    }
    res = scanner.run_scan("daily", symbols=list(data), fetch=lambda syms, **kw: data,
                           now=datetime(2026, 10, 27, 20, 0, tzinfo=NY))
    assert [h["symbol"] for h in res["hits"]] == ["BIG", "SMALL"]
    assert res["hits"][0]["setups"] == ["breakout_20d"] and res["scope"] == "symbols"
    assert scanner.read_results()["hits"][0]["symbol"] == "BIG"


def test_orb_ignores_yesterdays_session_and_waits_for_the_range():
    day = datetime(2026, 10, 26).date()                       # yesterday's bars only
    df = _five_min(day, [100, 100.5, 100.3, 100.4, 100.9, 101.2])
    assert scanner.orb_breakout(df, now=datetime(2026, 10, 27, 11, 0, tzinfo=NY)) is None
    assert "09:50" in scanner.orb_not_ready(datetime(2026, 10, 27, 6, 15, tzinfo=NY))
    assert "closed" in scanner.orb_not_ready(datetime(2026, 11, 26, 11, 0, tzinfo=NY))   # Thanksgiving
    assert scanner.orb_not_ready(datetime(2026, 10, 27, 10, 0, tzinfo=NY)) is None


def test_orb_scan_before_open_writes_a_note_without_fetching(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "RESULTS_PATH", tmp_path / "scan.json")

    def boom(*a, **k):
        raise AssertionError("must not fetch before the range is complete")

    res = scanner.run_scan("orb", symbols=["SPY"], fetch=boom, now=datetime(2026, 10, 27, 6, 15, tzinfo=NY))
    assert res["hits"] == [] and "09:50" in res["note"]
