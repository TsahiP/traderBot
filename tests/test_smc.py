"""LuxAlgo-style SMC engine, chart overlays and the analyst snapshot."""
import pandas as pd

from charts.smc import _prune, clean_smc_flags, detect_smc, run_luxalgo, snapshot_smc


def _ohlc(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="D")
    return pd.DataFrame(
        [{"Open": o, "High": h, "Low": l, "Close": c} for o, h, l, c in rows],
        index=idx,
    )


# pivot low 95 @1 (confirmed bar 6), pivot high 110 @8 (confirmed bar 13),
# close breaks 110 on bar 15
BREAK_ROWS = [
    (101, 102, 100, 101),
    (100, 101, 95, 96),
    (96, 99, 96, 98),
    (98, 102, 97, 101),
    (101, 104, 100, 103),
    (103, 106, 102, 105),
    (105, 108, 104, 107),
    (107, 109, 106, 108),
    (108, 110, 107, 109),
    (109, 109.5, 105, 106),
    (106, 107, 103, 104),
    (104, 105, 102, 103),
    (103, 104, 101, 102),
    (102, 103, 100, 101),
    (101, 104, 100.5, 103.5),
    (103.5, 111, 103, 110.5),
    (110.5, 111.5, 110, 111),
    (111, 111.5, 110.2, 111.2),
]
FAST_SWING = {"swing_length": 5}


def _small_bars(k: int, base: float = 100.0) -> list[tuple]:
    return [(base, base + 0.6, base - 0.4, base + 0.1)] * k


def test_flags_off_returns_empty():
    df = _ohlc([(10, 11, 9, 10.5)] * 12)
    overlay = detect_smc(df, window=10, flags=clean_smc_flags({}))
    assert overlay.zones == [] and overlay.structure == [] and overlay.liquidity == []


def test_prune_skips_element_after_removal_like_pine():
    assert _prune([1, 2, 3], lambda x: True) == [2]
    assert _prune([1, 2, 3], lambda x: x == 3) == [1, 2]


def test_internal_bos_and_order_block():
    res = run_luxalgo(_ohlc(BREAK_ROWS))
    bos = [e for e in res.structure if e.scope == "internal"]
    assert len(bos) == 1
    e = bos[0]
    assert (e.kind, e.side, e.pivot_bar, e.bar, e.price) == ("BOS", "bull", 8, 15, 110)
    # lowest low between the pivot and the break bar
    assert len(res.order_blocks) == 1
    ob = res.order_blocks[0]
    assert (ob.side, ob.x0, ob.y_lo, ob.y_hi) == ("bull", 13, 100, 103)


def test_order_block_mitigated_by_low():
    res = run_luxalgo(_ohlc(BREAK_ROWS + [(111, 111.5, 99.5, 110)]))
    assert res.order_blocks == []


def test_internal_break_hidden_when_same_level_as_swing():
    res = run_luxalgo(_ohlc(BREAK_ROWS), FAST_SWING)
    assert [(e.scope, e.kind, e.side) for e in res.structure] == [("swing", "BOS", "bull")]
    assert res.swing_bias == "bull"


def test_premium_discount_zones_and_strong_weak():
    res = run_luxalgo(_ohlc(BREAK_ROWS), FAST_SWING)
    assert (res.top, res.bottom) == (111.5, 95)
    assert res.top_label == "Weak High" and res.bottom_label == "Strong Low"
    z = res.zones()
    assert abs(z["premium"]["lo"] - (0.95 * 111.5 + 0.05 * 95)) < 1e-9
    assert abs(z["equilibrium"]["hi"] - (0.525 * 111.5 + 0.475 * 95)) < 1e-9
    assert abs(z["discount"]["hi"] - (0.95 * 95 + 0.05 * 111.5)) < 1e-9


def test_zones_lag_a_pivot_confirmed_on_the_last_bar():
    # bar 18 confirms the pivot low 100 @13; the indicator drew the zones first
    rows = BREAK_ROWS + [(111, 111.4, 110.4, 111)]
    assert run_luxalgo(_ohlc(rows), FAST_SWING).bottom == 95
    rows.append((111, 111.3, 110.5, 111))
    assert run_luxalgo(_ohlc(rows), FAST_SWING).bottom == 100


def test_bullish_fvg_needs_big_middle_bar_and_fills():
    rows = _small_bars(20)
    rows += [(100.2, 103.2, 100.1, 103.0), (103, 103.5, 101.0, 103.2)]
    rows += [(103.2, 103.6, 102.8, 103.3)] * 3
    res = run_luxalgo(_ohlc(rows))
    fvg = [z for z in res.fvgs if z.side == "bull"]
    assert len(fvg) == 1 and (fvg[0].y_lo, fvg[0].y_hi) == (100.6, 101.0)
    filled = run_luxalgo(_ohlc(rows + [(103, 103.2, 100.5, 101)]))
    assert not [z for z in filled.fvgs if z.side == "bull"]


def test_bearish_fvg_detected():
    rows = _small_bars(20)
    rows += [(100.0, 100.1, 97.0, 97.2), (97.2, 99.0, 96.5, 97.0)]
    rows += [(97.0, 97.4, 96.6, 97.1)] * 3
    res = run_luxalgo(_ohlc(rows))
    fvg = [z for z in res.fvgs if z.side == "bear"]
    assert len(fvg) == 1 and (fvg[0].y_lo, fvg[0].y_hi) == (99.0, 99.6)


def _sawtooth(periods: int) -> list[tuple]:
    highs = [101, 102, 103, 105, 103, 102, 101, 100.5]
    rows = []
    for _ in range(periods):
        rows += [(h - 0.5, h, h - 1, h - 0.4) for h in highs]
    return rows


def test_equal_highs_lows_need_atr_warmup():
    res = run_luxalgo(_ohlc(_sawtooth(30)))
    eqh = [e for e in res.equal_levels if e.side == "bear"]
    eql = [e for e in res.equal_levels if e.side == "bull"]
    assert eqh and eql
    assert all(e.level0 == e.level1 == 105 for e in eqh)
    assert min(e.bar1 for e in res.equal_levels) >= 190   # ATR(200) is na before


def test_detect_smc_window_coordinates():
    df = _ohlc(BREAK_ROWS)
    overlay = detect_smc(df, window=10, flags={"zones": True, "structure": True, "liquidity": False})
    (m,) = overlay.structure
    assert (m.bar, m.pivot_bar) == (15 - 8, 8 - 8)
    (ob,) = [z for z in overlay.zones if z.kind == "order_block"]
    assert ob.x0 == 13 - 8


def test_snapshot_reads_luxalgo_state():
    snap = snapshot_smc(_ohlc(BREAK_ROWS), settings=FAST_SWING)
    assert snap["ok"] is True
    assert snap["range"]["lo"] == 95 and snap["range"]["hi"] == 111.5
    assert snap["strong_weak"]["high"]["label"] == "Weak High"
    assert snap["strong_weak"]["low"]["label"] == "Strong Low"
    assert snap["price_zone"] == "premium" and snap["in_zone"] == "premium"
    assert snap["swing_bias"] == "bull"
    assert snap["last_bos"]["scope"] == "swing" and snap["last_bos"]["price"] == 110


def test_snapshot_demand_and_wall():
    snap = snapshot_smc(_ohlc(BREAK_ROWS))
    assert snap["demand_below"] == {"kind": "order_block", "side": "bull", "lo": 100.0,
                                    "hi": 103.0, "bar": 13, "scope": "internal"}
    assert snap["supply_above"] is None and snap["wall"] is None
    assert snap["range"] is None and snap["price_zone"] == "n/a"   # no 50-bar swing yet


def test_snapshot_not_enough_bars():
    assert snapshot_smc(_ohlc([(10, 11, 9, 10.5)] * 3)) == {"ok": False, "error": "not enough bars"}
