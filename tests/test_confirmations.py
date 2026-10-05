"""Confluence checks: pass, fail, and not-enough-history for long and short."""
import pandas as pd

import confirmations


def _frame(closes, volume=None, spread=0.5):
    n = len(closes)
    close = [float(c) for c in closes]
    if volume is None:
        volume = [100.0] * n
    return pd.DataFrame(
        {
            "Open": close,
            "High": [c + spread for c in close],
            "Low": [c - spread for c in close],
            "Close": close,
            "Volume": list(volume),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )


def _check(result, check_id):
    return next(c for c in result["checks"] if c["id"] == check_id)


def test_trend_long_and_short():
    rising = _frame([100 + i for i in range(220)])
    long = confirmations.evaluate(rising, "long", 3)
    short = confirmations.evaluate(rising, "short", 3)
    assert _check(long, "trend")["passed"] is True
    assert _check(short, "trend")["passed"] is False

    falling = _frame([400 - i for i in range(220)])
    assert _check(confirmations.evaluate(falling, "short", 3), "trend")["passed"] is True
    assert _check(confirmations.evaluate(falling, "long", 3), "trend")["passed"] is False


def test_trend_not_enough_bars_is_excluded_from_score():
    df = _frame([100 + i * 0.2 for i in range(40)])
    result = confirmations.evaluate(df, "long", 3)
    assert _check(result, "trend")["passed"] is None
    assert result["total"] == sum(1 for c in result["checks"] if c["passed"] is not None)
    assert result["score"] == sum(1 for c in result["checks"] if c["passed"] is True)
    assert result["total"] < 5


def test_volume_spike_pass_fail_and_missing():
    closes = [100.0] * 30
    spike = _frame(closes, volume=[100.0] * 29 + [200.0])
    quiet = _frame(closes, volume=[100.0] * 29 + [100.0])
    dead = _frame(closes, volume=[0.0] * 30)
    assert _check(confirmations.evaluate(spike, "long", 1), "volume")["passed"] is True
    assert _check(confirmations.evaluate(quiet, "long", 1), "volume")["passed"] is False
    assert _check(confirmations.evaluate(dead, "long", 1), "volume")["passed"] is None


def test_rsi_long_after_drop_and_short_after_rise():
    drop = [100.0] * 30 + [100 - i * 4 for i in range(1, 8)]
    long = confirmations.evaluate(_frame(drop), "long", 3)
    assert float(confirmations.rsi(pd.Series(drop, dtype=float)).iloc[-1]) < 40
    assert _check(long, "rsi")["passed"] is True
    assert _check(confirmations.evaluate(_frame(drop), "short", 3), "rsi")["passed"] is False

    rise = [100.0] * 30 + [100 + i * 4 for i in range(1, 8)]
    short = confirmations.evaluate(_frame(rise), "short", 3)
    assert _check(short, "rsi")["passed"] is True
    assert _check(confirmations.evaluate(_frame(rise), "long", 3), "rsi")["passed"] is False


def test_rsi_not_enough_bars():
    result = confirmations.evaluate(_frame([100.0] * 10), "long", 1)
    assert _check(result, "rsi")["passed"] is None


def test_macd_follows_histogram_direction():
    slow = [100 + i * 0.05 for i in range(50)]
    fast = [slow[-1] + i * 3 for i in range(1, 12)]
    df = _frame(slow + fast)
    hist = confirmations.macd_hist(df["Close"])
    rising = float(hist.iloc[-1]) > float(hist.iloc[-2])
    macd = _check(confirmations.evaluate(df, "long", 3), "macd")
    assert macd["passed"] is rising
    short = _check(confirmations.evaluate(df, "short", 3), "macd")
    assert short["passed"] is (not rising)


def test_support_long_touch_and_miss():
    dip = _frame([100.0] * 60)
    dip.loc[dip.index[-1], "Low"] = 90
    touched = _check(confirmations.evaluate(dip, "long", 1), "support")
    assert touched["passed"] is True

    climb = _frame([100 + i for i in range(80)], spread=0.2)
    missed = _check(confirmations.evaluate(climb, "long", 3), "support")
    assert missed["passed"] is False


def test_support_short_touches_upper_band():
    pop = _frame([100.0] * 60)
    pop.loc[pop.index[-1], "High"] = 130
    assert _check(confirmations.evaluate(pop, "short", 1), "support")["passed"] is True


def test_format_lines_and_below_min():
    df = _frame([100.0] * 25, volume=[0.0] * 25)
    result = confirmations.evaluate(df, "long", 1)
    lines = confirmations.format_lines(result)
    assert lines[0].startswith("Confluence:")
    assert any(line.startswith("~") for line in lines)
    assert confirmations.below_min({"score": 2, "total": 5}, 3) is True
    assert confirmations.below_min({"score": 3, "total": 5}, 3) is False
    assert confirmations.below_min({"score": 0, "total": 0}, 3) is False
    assert confirmations.format_lines(result, details=False) == [lines[0]]
    assert confirmations.failed_required(result, {"volume": True, "trend": False}) == []
    missed = {
        "checks": [
            {"id": "trend", "passed": False},
            {"id": "volume", "passed": None},
            {"id": "rsi", "passed": True},
        ]
    }
    assert confirmations.failed_required(missed, {"trend": True, "volume": True, "rsi": False}) == ["trend"]
