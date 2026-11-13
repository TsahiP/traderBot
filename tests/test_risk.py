"""Risk gate: every check blocks on its own, PASS when all fit, limits switch off,
long and short."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import core.risk as risk

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 27, 10, 30, tzinfo=NY)


def _check(**over):
    args = dict(
        symbol="SPY", qty=10, entry=100.0, stop=95.0, target=110.0, now_ny=NOW,
        equity=100_000.0, cash=100_000.0, entries_today=[], limits=risk.RiskLimits(),
    )
    args.update(over)
    return risk.check_entry(**args)


def _blocked_by(verdict, name):
    return not verdict.ok and any(r.startswith(name + ":") for r in verdict.reasons)


def test_pass_when_everything_fits():
    v = _check()
    assert v.ok and v.summary() == "PASS"


def test_max_loss_helper():
    assert risk.max_loss(10, 100.0, 95.0) == 50.0
    assert risk.max_loss(10, 100.0, None) is None


def test_stop_required_and_on_the_right_side():
    assert _blocked_by(_check(stop=None), "stop")
    assert _blocked_by(_check(stop=100.0), "stop")
    assert _blocked_by(_check(stop=101.0), "stop")
    no_stop_ok = _check(stop=None, limits=risk.RiskLimits(require_stop=False, min_reward_risk=None))
    assert no_stop_ok.ok


def test_loss_per_trade():
    assert _blocked_by(_check(qty=50), "loss")          # 50 x 5 = 250 > 200
    assert not _blocked_by(_check(qty=40), "loss")      # 200 is at the limit, allowed


def test_reward_risk():
    assert _blocked_by(_check(target=105.0), "reward_risk")   # 1.0 < 1.5
    assert _blocked_by(_check(target=None), "reward_risk")
    assert _check(target=107.5).ok                            # exactly 1.5


def test_daily_loss_counts_todays_entries():
    used = [{"symbol": "QQQ", "max_loss": 200.0}, {"symbol": "AAPL", "max_loss": 260.0}]
    assert _blocked_by(_check(entries_today=used), "daily_loss")   # 460 + 50 > 500
    assert not _blocked_by(_check(entries_today=used[:1]), "daily_loss")


def test_trades_per_day_and_duplicate():
    three = [{"symbol": s, "max_loss": 1.0} for s in ("A", "B", "C")]
    assert _blocked_by(_check(entries_today=three), "trades")
    assert _blocked_by(_check(entries_today=[{"symbol": "SPY", "max_loss": 1.0}]), "duplicate")


def test_entry_window():
    assert _blocked_by(_check(now_ny=NOW.replace(hour=9, minute=35)), "window")
    assert _blocked_by(_check(now_ny=NOW.replace(hour=15, minute=45)), "window")
    open_all_day = risk.RiskLimits(no_entries_before=None, no_entries_after=None)
    assert _check(now_ny=NOW.replace(hour=9, minute=31), limits=open_all_day).ok


def test_position_cap_and_cash_buffer():
    assert _blocked_by(_check(equity=3_000.0, cash=3_000.0), "position")   # 1000 / 3000 = 33%
    assert _blocked_by(_check(cash=1_500.0), "cash")                       # 500 left < 10k buffer
    no_buffer = risk.RiskLimits(cash_buffer_pct=0)
    assert _blocked_by(_check(cash=900.0, limits=no_buffer), "cash")       # would need margin


def test_limits_switch_off():
    off = risk.RiskLimits(
        max_loss_per_trade_usd=None, max_daily_loss_usd=None, max_trades_per_day=None,
        min_reward_risk=None, max_position_pct=None, cash_buffer_pct=None,
        no_entries_before=None, no_entries_after=None,
    )
    assert _check(qty=1000, target=None, cash=1_000_000.0, limits=off).ok


def test_summary_lists_every_reason():
    v = _check(qty=50, target=101.0)
    assert v.summary().startswith("BLOCK: ")
    assert "loss:" in v.summary() and "reward_risk:" in v.summary()


def test_size_position_picks_the_tightest_limit():
    # max loss: 200 / 5 = 40 shares; position cap 25% of 10k = 2500 / 100 = 25 shares
    assert risk.size_position(entry=100.0, stop=95.0, max_loss_usd=200.0, equity=10_000.0,
                              cash=10_000.0, max_position_pct=25.0) == (25, "position_cap")
    assert risk.size_position(entry=100.0, stop=95.0, max_loss_usd=200.0, equity=100_000.0,
                              cash=100_000.0, max_position_pct=25.0) == (40, "max_loss")
    # cash after a 10% buffer: 3000 - 1000 = 2000 -> 20 shares
    assert risk.size_position(entry=100.0, stop=95.0, max_loss_usd=200.0, equity=10_000.0,
                              cash=3_000.0, max_position_pct=50.0, cash_buffer_pct=10.0) == (20, "cash")
    assert risk.size_position(entry=100.0, stop=95.0, max_loss_usd=200.0, max_shares=10) == (10, "max_shares")
    assert risk.size_position(entry=100.0, stop=101.0, max_loss_usd=200.0) == (0, "stop")


# ---------------------------------------------------------------- short side

def _short(**over):
    args = dict(side="short", stop=105.0, target=90.0, buying_power=200_000.0)
    args.update(over)
    return _check(**args)


def test_short_pass_when_everything_fits():
    assert _short().ok


def test_short_max_loss_helper():
    assert risk.max_loss(10, 100.0, 105.0, side="short") == 50.0
    assert risk.max_loss(10, 100.0, None, side="short") is None


def test_short_stop_must_be_above_the_entry():
    assert _blocked_by(_short(stop=100.0), "stop")
    assert _blocked_by(_short(stop=95.0), "stop")


def test_short_loss_and_reward_risk_are_mirrored():
    assert _blocked_by(_short(qty=50), "loss")                 # 50 x 5 = 250 > 200
    assert _blocked_by(_short(target=95.0), "reward_risk")     # 1.0 < 1.5
    assert _blocked_by(_short(target=101.0), "reward_risk")    # target not below the entry
    assert _short(target=92.5).ok                              # exactly 1.5


def test_short_daily_loss_counts_the_short_risk():
    used = [{"symbol": "QQQ", "max_loss": 460.0}]
    assert _blocked_by(_short(entries_today=used), "daily_loss")   # 460 + 50 > 500


def test_short_uses_buying_power_not_cash():
    # no cash at all, plenty of buying power: a short is fine
    assert _short(cash=0.0).ok
    # buying power that would dip under the 10% buffer blocks
    v = _short(buying_power=10_500.0)
    assert _blocked_by(v, "buying_power") and not _blocked_by(v, "cash")


def test_short_position_cap_counts_notional():
    assert _blocked_by(_short(equity=3_000.0, buying_power=100_000.0), "position")


def test_unknown_side_raises():
    with pytest.raises(ValueError):
        _check(side="sideways")


def test_size_position_short():
    # risk 5 a share above the entry: 200 / 5 = 40
    assert risk.size_position(entry=100.0, stop=105.0, max_loss_usd=200.0, side="short") == (40, "max_loss")
    # buying power after a 10% buffer of 10k equity: 3000 - 1000 = 2000 -> 20 shares
    assert risk.size_position(entry=100.0, stop=105.0, max_loss_usd=200.0, equity=10_000.0,
                              cash=0.0, buying_power=3_000.0, max_position_pct=50.0,
                              cash_buffer_pct=10.0, side="short") == (20, "buying_power")
    assert risk.size_position(entry=100.0, stop=99.0, max_loss_usd=200.0, side="short") == (0, "stop")
