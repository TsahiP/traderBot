"""NYSE trading calendar: holidays, 1:00 PM early closes and session hours.

Rule-based, so it works for any year without a holiday file: New Year's Day,
MLK Day, Washington's Birthday, Good Friday, Memorial Day, Juneteenth (2022+),
Independence Day, Labor Day, Thanksgiving and Christmas, with NYSE's
weekend observance (Saturday -> Friday, Sunday -> Monday; a Saturday New
Year's Day is not observed). Early closes: July 3 (Mon-Thu), the day after
Thanksgiving, and Christmas Eve when it is a trading day. Checked against
nyse.com/markets/hours-calendars for 2026-2027.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def holidays(year: int) -> frozenset[date]:
    days = {
        _nth_weekday(year, 1, 0, 3),            # MLK Day
        _nth_weekday(year, 2, 0, 3),            # Washington's Birthday
        _easter(year) - timedelta(days=2),      # Good Friday
        _last_weekday(year, 5, 0),              # Memorial Day
        _observed(date(year, 7, 4)),            # Independence Day
        _nth_weekday(year, 9, 0, 1),            # Labor Day
        _nth_weekday(year, 11, 3, 4),           # Thanksgiving
        _observed(date(year, 12, 25)),          # Christmas
    }
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:                 # Saturday New Year's is not observed
        days.add(_observed(new_year))
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    return frozenset(d for d in days if d.year == year)


@lru_cache(maxsize=64)
def early_closes(year: int) -> frozenset[date]:
    out = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1)}  # day after Thanksgiving
    jul3 = date(year, 7, 3)
    if jul3.weekday() <= 3:                                   # Mon-Thu
        out.add(jul3)
    xmas_eve = date(year, 12, 24)
    if xmas_eve.weekday() < 5 and xmas_eve not in holidays(year):
        out.add(xmas_eve)
    return frozenset(out)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def session(d: date) -> tuple[datetime, datetime] | None:
    """(open, close) as NY datetimes, or None when the market is closed all day."""
    if not is_trading_day(d):
        return None
    close = EARLY_CLOSE if d in early_closes(d.year) else CLOSE
    return datetime.combine(d, OPEN, NY), datetime.combine(d, close, NY)


def is_open(at: datetime) -> bool:
    at = at.astimezone(NY)
    s = session(at.date())
    return bool(s and s[0] <= at < s[1])


def previous_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def stock_bar_due(timeframe: str, now: datetime, tf_minutes: int | None) -> bool:
    """Should a stock job scan now? Intraday: only bars ending inside a
    session (the last partial bar is allowed). Daily: only the midnight run
    after a trading day. Weekly and unknown timeframes always run."""
    now = now.astimezone(NY)
    if timeframe == "1d":
        return is_trading_day((now - timedelta(hours=12)).date())
    if not tf_minutes:
        return True
    s = session(now.date())
    if s is None:
        return False
    minute = (now.hour * 60 + now.minute) // tf_minutes * tf_minutes
    bar_end = datetime.combine(now.date(), time(minute // 60, minute % 60), NY)
    return s[0] < bar_end <= s[1] + timedelta(minutes=tf_minutes)
