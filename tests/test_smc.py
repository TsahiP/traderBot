"""SMC detection for alert chart overlays."""
import pandas as pd

from smc import clean_smc_flags, detect_smc


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
