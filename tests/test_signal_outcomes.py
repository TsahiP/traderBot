"""Target/stop outcome resolution and the stats buckets built from the log."""
import json

import pandas as pd

import config
import signal_outcomes
import signals


def _bars(rows):
    """rows: (high, low) after an entry bar at index 0."""
    highs = [100.0] + [h for h, _ in rows]
    lows = [100.0] + [l for _, l in rows]
    n = len(highs)
    return pd.DataFrame(
        {
            "Open": [100.0] * n,
            "High": highs,
            "Low": lows,
            "Close": [100.0] * n,
            "Volume": [1.0] * n,
        },
        index=pd.date_range("2026-01-01", periods=n, freq="D"),
    )


def _entry(**overrides):
    idx = pd.Timestamp("2026-01-01")
    item = {
        "symbol": "SPY",
        "timeframe": "1d",
        "direction": "long",
        "bar_ts": signal_outcomes.bar_key(idx),
        "stop": 95.0,
        "target": 110.0,
    }
    item.update(overrides)
    return item


def test_long_target_before_stop_is_win():
    df = _bars([(105, 96), (111, 100)])
    assert signal_outcomes.resolve_outcome(df, _entry()) == "win"


def test_long_stop_before_target_is_loss():
    df = _bars([(104, 94)])
    assert signal_outcomes.resolve_outcome(df, _entry()) == "loss"


def test_same_bar_stop_and_target_is_loss():
    df = _bars([(112, 90)])
    assert signal_outcomes.resolve_outcome(df, _entry()) == "loss"


def test_short_target_is_win():
    df = _bars([(104, 88)])
    entry = _entry(direction="short", stop=105.0, target=90.0)
    assert signal_outcomes.resolve_outcome(df, entry) == "win"


def test_expires_after_max_bars():
    df = _bars([(101, 99)] * 3)
    assert signal_outcomes.resolve_outcome(df, _entry(), max_bars=3) == "expired"
    assert signal_outcomes.resolve_outcome(df, _entry(), max_bars=4) is None


def test_missing_target_is_skipped():
    df = _bars([(120, 96)])
    assert signal_outcomes.resolve_outcome(df, _entry(target=None)) is None


def test_update_pending_writes_outcome(monkeypatch, tmp_path):
    path = tmp_path / "signal_log.json"
    monkeypatch.setattr(config, "SIGNAL_LOG_PATH", path)
    df = _bars([(111, 99)])
    entry = _entry()
    entry.update({
        "key": "default|SPY|1d|2026-01-01 00:00|bullish_engulfing",
        "confluence": {"score": 4, "total": 5, "checks": []},
    })
    path.write_text(json.dumps([entry]), encoding="utf-8")

    signal_outcomes.update_pending({("SPY", "1d"): df})

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved[0]["outcome"] == "win"
    stats = signals.signal_stats()
    assert stats["sent"]["wins"] == 1
    assert stats["by_score"] == [{
        "score": 4,
        "total": 1,
        "wins": 1,
        "losses": 0,
        "expired": 0,
        "pending": 0,
    }]


def test_read_signals_hides_filtered_and_stats_split_them(monkeypatch, tmp_path):
    path = tmp_path / "signal_log.json"
    monkeypatch.setattr(config, "SIGNAL_LOG_PATH", path)
    path.write_text(json.dumps([
        {"key": "a", "filtered": True, "confluence": {"score": 1, "total": 5}},
        {"key": "b", "confluence": {"score": 4, "total": 5}, "outcome": "loss"},
    ]), encoding="utf-8")
    assert [e["key"] for e in signals.read_signals()] == ["b"]
    assert [e["key"] for e in signals.read_signals(include_filtered=True)] == ["b", "a"]
    stats = signals.signal_stats()
    assert stats["filtered"]["total"] == 1
    assert stats["filtered"]["pending"] == 1
    assert stats["sent"]["losses"] == 1
