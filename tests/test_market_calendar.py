"""NYSE calendar: holidays and early closes match nyse.com for 2026-2027."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import market_calendar as mc

NY = ZoneInfo("America/New_York")


def test_2026_holidays_and_early_closes():
    assert mc.holidays(2026) == {
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
        date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
        date(2026, 11, 26), date(2026, 12, 25),
    }
    assert mc.early_closes(2026) == {date(2026, 11, 27), date(2026, 12, 24)}


def test_2027_holidays_and_early_closes():
    assert mc.holidays(2027) == {
        date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
        date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
        date(2027, 11, 25), date(2027, 12, 24),
    }
    assert mc.early_closes(2027) == {date(2027, 11, 26)}


def test_saturday_new_year_not_observed():
    assert date(2021, 12, 31) not in mc.holidays(2021)   # Jan 1 2022 was a Saturday
    assert date(2022, 1, 1) not in mc.holidays(2022)


def test_sessions_and_is_open():
    assert mc.session(date(2026, 10, 31)) is None                   # Saturday
    _, close = mc.session(date(2026, 11, 27))
    assert close.hour == 13
    assert mc.is_open(datetime(2026, 10, 27, 10, 0, tzinfo=NY))
    assert not mc.is_open(datetime(2026, 10, 27, 16, 0, tzinfo=NY))
    assert not mc.is_open(datetime(2026, 11, 26, 11, 0, tzinfo=NY))  # Thanksgiving
    assert mc.previous_trading_day(date(2026, 11, 30)) == date(2026, 11, 27)


def test_stock_bar_due():
    tue = lambda h, m: datetime(2026, 10, 27, h, m, 45, tzinfo=NY)
    assert mc.stock_bar_due("5m", tue(10, 5), 5)
    assert not mc.stock_bar_due("5m", tue(9, 30), 5)     # pre-market bar
    assert mc.stock_bar_due("5m", tue(16, 0), 5)         # last bar of the day
    assert not mc.stock_bar_due("5m", tue(20, 0), 5)
    assert mc.stock_bar_due("1h", tue(16, 0), 60)
    assert not mc.stock_bar_due("15m", datetime(2026, 11, 27, 14, 0, tzinfo=NY), 15)  # after early close
    assert mc.stock_bar_due("1d", datetime(2026, 10, 28, 0, 0, 45, tzinfo=NY), None)   # after Tue
    assert not mc.stock_bar_due("1d", datetime(2026, 11, 1, 0, 0, 45, tzinfo=NY), None)  # after Sat
    assert mc.stock_bar_due("1w", datetime(2026, 11, 2, 0, 0, 45, tzinfo=NY), None)
