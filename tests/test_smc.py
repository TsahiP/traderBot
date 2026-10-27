"""SMC detection for alert chart overlays."""
import pandas as pd

from smc import snapshot_smc, clean_smc_flags, detect_smc, _swing_highs_lows


def _ohlc(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(
        [{"Open": o, "High": h, "Low": l, "Close": c} for o, h, l, c in rows],
        index=idx,
    )


def test_flags_off_returns_empty():
    df = _ohlc([(10, 11, 9, 10.5)] * 12)
    overlay = detect_smc(df, window=10, flags=clean_smc_flags({}))
    assert overlay.zones == []
    assert overlay.structure == []
    assert overlay.liquidity == []


def test_bullish_fvg_detected():
    # gap: bar2 low above bar0 high
    rows = [
        (100, 101, 99, 100),
        (100, 102, 99.5, 101.5),
        (103, 105, 102.5, 104),  # low 102.5 > high 101 of bar0
    ]
    rows += [(104, 105, 103, 104.5)] * 10
    df = _ohlc(rows)
    overlay = detect_smc(df, window=12, flags={"zones": True, "structure": False, "liquidity": False})
    fvgs = [z for z in overlay.zones if z.kind == "fvg" and z.side == "bull"]
    assert len(fvgs) >= 1


def test_structure_bos_on_break():
    # 5-bar swing high at 110, then close breaks above it
    rows = [
        (100, 101, 99, 100),
        (100, 102, 99, 101),
        (101, 103, 100, 102),
        (102, 104, 101, 103),
        (103, 110, 102, 108),  # swing high
        (108, 109, 106, 107),
        (107, 108, 105, 106),
        (106, 107, 104, 105),
        (105, 106, 103, 104),
        (104, 112, 103, 111),  # close above swing high
    ]
    rows += [(111, 112, 110, 111)] * 8
    df = _ohlc(rows)
    overlay = detect_smc(df, window=18, flags={"zones": False, "structure": True, "liquidity": False})
    assert any(m.kind in ("BOS", "CHoCH") for m in overlay.structure)


def test_liquidity_equal_highs_sweep():
    # two 5-bar swing highs at 105, then sweep
    pad = (100, 101, 99, 100)
    peak = (104, 105, 103, 104)
    rows = [pad, pad, pad, peak, (103, 104, 102, 103), (102, 103, 101, 102)]
    rows += [pad, pad, pad, peak, (103, 104, 102, 103), (102, 103, 101, 102)]
    rows.append((102, 106, 101, 103))  # wick above 105, close below
    rows += [pad] * 12
    df = _ohlc(rows)
    overlay = detect_smc(df, window=25, flags={"zones": False, "structure": False, "liquidity": True})
    assert len(overlay.liquidity) >= 1

# ------------------------------------------------------- snapshot_smc tests

def test_swing_radius_parametrized():
    highs = [99, 104, 99, 105, 99, 99, 105, 99]
    df = pd.DataFrame(
        [{"Open": h, "High": h, "Low": h - 0.5, "Close": h} for h in highs],
        index=pd.date_range("2026-01-01", periods=len(highs), freq="D"),
    )
    sh2, _ = _swing_highs_lows(df, radius=2)
    sh3, _ = _swing_highs_lows(df, radius=3)
    assert 3 in sh2
    assert 3 not in sh3


def _premium_closes_ok(rows):
    """Known range: swing low 100 at bar 2, swing high 120 at bar 8."""
    return rows


def test_snapshot_premium_discount_zones():
    def build(end_close):
        return _ohlc([
            (100.2, 100.9, 100.6, 100.8),
            (100.6, 101.0, 100.5, 100.9),
            (100.3, 100.9, 100.0, 100.6),   # swing low 100.0
            (100.7, 101.2, 100.3, 101.0),
            (101.0, 101.5, 100.4, 101.3),
            (101.3, 106.0, 101.2, 104.8),
            (104.8, 108.0, 104.5, 107.5),
            (107.5, 119.0, 107.0, 118.5),
            (118.5, 120.0, 117.5, 119.0),   # swing high 120
            (119.0, 119.4, 112.5, 113.0),
            (113.5, 115.0, 112.2, 114.0),
            (112.3, 116.0, 111.8, end_close),
        ])

    snap = snapshot_smc(build(115.0))
    assert snap["ok"] is True
    assert snap["range"]["lo"] == 100.0 and snap["range"]["hi"] == 120.0
    assert snap["range"]["mid"] == 110.0
    assert snap["price_zone"] == "premium"
    assert snap["price_position_pct"] == 75.0
    assert snap["near_equilibrium"] is False

    snap_low = snapshot_smc(build(108.0))
    assert snap_low["price_zone"] == "discount"
    assert snap_low["price_position_pct"] == 40.0


def test_snapshot_demand_block_below():
    df = _ohlc([
        (100.0, 100.8, 99.6, 100.4),
        (100.4, 101.2, 100.1, 101.1),
        (101.1, 102.2, 100.5, 102.0),
        (102.0, 103.0, 101.4, 101.6),   # swing high 103
        (101.6, 101.7, 100.8, 101.0),   # down candle -> bull OB body [101.0, 101.6]
        (101.1, 101.4, 101.05, 101.35),
        (101.35, 103.5, 101.3, 103.2),  # close > 103 -> BOS, low stays above OB lo
        (103.2, 103.6, 102.0, 103.5),
    ])
    snap = snapshot_smc(df)
    assert snap["demand_below"] is not None
    assert snap["demand_below"]["lo"] == 101.0
    assert snap["demand_below"]["hi"] == 101.6
    assert snap["wall"] is None
    assert snap["last_bos"] is not None and snap["last_bos"]["side"] == "bull"


def test_snapshot_supply_wall_above():
    df = _ohlc([
        (101.0, 101.4, 100.4, 101.0),
        (101.0, 101.6, 100.6, 101.3),
        (101.3, 101.6, 100.2, 100.9),
        (100.9, 100.8, 99.7, 100.2),    # swing low 99.7
        (100.2, 101.3, 100.15, 101.2),  # up candle -> bear OB body [101.2, 101.6]
        (101.2, 101.8, 100.4, 101.6),
        (101.5, 101.55, 99.4, 99.5),    # close < 99.7 -> breakdown, high < OB hi
        (99.4, 99.6, 99.0, 99.3),
    ])
    snap = snapshot_smc(df)
    assert snap["supply_above"] is not None
    assert snap["supply_above"]["lo"] == 101.2
    assert snap["supply_above"]["hi"] == 101.6
    assert snap["wall"]["price"] == 101.2
    # (101.2 - 99.3) / 99.3 = 1.913%
    assert abs(snap["wall"]["distance_pct"] - 1.91) < 0.01
    assert snap["supply_above"]["lo"] > snap["price"]


def test_snapshot_open_fvg():
    df = _ohlc([
        (100.0, 100.8, 99.8, 100.6),
        (100.9, 101.0, 100.85, 101.0),  # low above 100.8 -> first FVG stays open
        (103.0, 103.4, 102.5, 103.0),   # low > high[-3] -> bull FVG [100.8, 102.5]
        (103.0, 103.2, 102.6, 103.1),
        (103.1, 103.3, 102.7, 103.2),
    ])
    snap = snapshot_smc(df)
    assert any(
        z["side"] == "bull" and z["lo"] == 100.8 and z["hi"] == 102.5
        for z in snap["open_fvgs"]
    )


def test_snapshot_equal_lows_swept():
    pad = (100, 100.5, 99.0, 99.8)
    lowc = (99.8, 100.2, 98.0, 99.5)    # swing low 98.0
    rows = [pad, pad, pad, lowc, pad, pad, pad, lowc, pad, pad]
    rows.append((99.8, 100.0, 97.6, 99.6))  # sweep: low < 98, close > 98
    rows += [pad] * 12
    snap = snapshot_smc(_ohlc(rows))
    assert snap["eql"] is not None
    assert abs(snap["eql"]["level"] - 98.0) < 1e-9
    assert snap["eql"]["sweep_bar"] is not None


def test_snapshot_not_enough_bars():
    assert snapshot_smc(_ohlc([(10, 11, 9, 10.5)] * 3)) == {"ok": False, "error": "not enough bars"}
