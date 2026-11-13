"""Optional technical confluence checks for candlestick signal alerts.

Each check returns passed=True/False, or None when there is not enough
history (or no volume). None is left out of the score denominator.
"""
import pandas as pd

EMA_TREND = 200
EMA_SUPPORT = 50
RSI_PERIOD = 14
VOLUME_LOOKBACK = 20
VOLUME_MULT = 1.5
BB_PERIOD = 20
BB_STD = 2.0
MACD_MIN_BARS = 35


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / period, adjust=False).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_up / avg_down
    return 100 - 100 / (1 + rs)


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    macd = ema(close, fast) - ema(close, slow)
    return macd - ema(macd, signal)


def bollinger(close: pd.Series, period: int = BB_PERIOD, n_std: float = BB_STD):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    return mid, mid + n_std * std, mid - n_std * std


def volume_ratio(volume: pd.Series, lookback: int = VOLUME_LOOKBACK) -> float | None:
    """Last bar vs the mean of the previous `lookback` bars. None if no baseline volume."""
    if len(volume) < lookback + 1:
        return None
    baseline = volume.iloc[-(lookback + 1):-1]
    mean = float(baseline.mean())
    if mean <= 0:
        return None
    return float(volume.iloc[-1]) / mean


def below_min(result: dict, min_score: int) -> bool:
    """True when a scored signal is under the minimum. Unscored (total 0) is not blocked."""
    if result["total"] <= 0:
        return False
    return result["score"] < int(min_score)


def failed_required(result: dict, checks: dict | None) -> list[str]:
    """Ids of required checks that failed. A missing or n/a check does not fail."""
    required = {check_id for check_id, on in (checks or {}).items() if on}
    return [
        check["id"]
        for check in result["checks"]
        if check["id"] in required and check["passed"] is False
    ]


def format_lines(result: dict, details: bool = True) -> list[str]:
    lines = [f"Confluence: {result['score']}/{result['total']}"]
    if not details:
        return lines
    for check in result["checks"]:
        if check["passed"] is None:
            mark = "~"
        elif check["passed"]:
            mark = "+"
        else:
            mark = "-"
        lines.append(f"{mark} {check['label']}: {check['detail']}")
    return lines


def _trend_check(close: pd.Series, direction: str) -> tuple[bool | None, str]:
    if len(close) < EMA_TREND:
        return None, "not enough bars"
    level = float(ema(close, EMA_TREND).iloc[-1])
    last = float(close.iloc[-1])
    above = last > level
    if direction == "long":
        return above, "close above EMA 200" if above else "close below EMA 200"
    return (not above), "close below EMA 200" if not above else "close above EMA 200"


def _volume_check(volume: pd.Series) -> tuple[bool | None, str]:
    ratio = volume_ratio(volume)
    if ratio is None:
        return None, "no volume"
    passed = ratio >= VOLUME_MULT
    return passed, f"{ratio:.1f}x average"


def _rsi_check(close: pd.Series, direction: str) -> tuple[bool | None, str]:
    if len(close) < RSI_PERIOD + 3:
        return None, "not enough bars"
    window = rsi(close).iloc[-3:]
    if window.isna().any():
        return None, "not enough bars"
    current = float(window.iloc[-1])
    detail = f"RSI {current:.0f}"
    if direction == "long":
        dipped = bool((window < 40).any())
        return dipped and current <= 70, detail
    peaked = bool((window > 60).any())
    return peaked and current >= 30, detail


def _macd_check(close: pd.Series, direction: str) -> tuple[bool | None, str]:
    if len(close) < MACD_MIN_BARS:
        return None, "not enough bars"
    hist = macd_hist(close)
    rising = float(hist.iloc[-1]) > float(hist.iloc[-2])
    if direction == "long":
        return rising, "histogram rising" if rising else "histogram falling"
    return (not rising), "histogram falling" if not rising else "histogram rising"


def _support_check(df: pd.DataFrame, direction: str, pattern_bars: int) -> tuple[bool | None, str]:
    if len(df) < EMA_SUPPORT:
        return None, "not enough bars"
    ema50 = float(ema(df["Close"], EMA_SUPPORT).iloc[-1])
    _mid, upper, lower = bollinger(df["Close"])
    if pd.isna(lower.iloc[-1]) or pd.isna(upper.iloc[-1]):
        return None, "not enough bars"
    window = df.iloc[-max(1, pattern_bars):]
    if direction == "long":
        low = float(window["Low"].min())
        at_ema = low <= ema50
        at_band = low <= float(lower.iloc[-1])
        if at_ema or at_band:
            parts = []
            if at_ema:
                parts.append("EMA 50")
            if at_band:
                parts.append("lower band")
            return True, "low at " + " and ".join(parts)
        return False, "low above EMA 50 and lower band"
    high = float(window["High"].max())
    at_ema = high >= ema50
    at_band = high >= float(upper.iloc[-1])
    if at_ema or at_band:
        parts = []
        if at_ema:
            parts.append("EMA 50")
        if at_band:
            parts.append("upper band")
        return True, "high at " + " and ".join(parts)
    return False, "high below EMA 50 and upper band"


def evaluate(df: pd.DataFrame, direction: str, pattern_bars: int) -> dict:
    """Score a completed pattern on the last bar of `df`."""
    if direction not in ("long", "short"):
        raise ValueError(f"direction must be long or short, got {direction!r}")
    specs = [
        ("trend", "Trend", _trend_check(df["Close"], direction)),
        ("volume", "Volume", _volume_check(df["Volume"])),
        ("rsi", "RSI", _rsi_check(df["Close"], direction)),
        ("macd", "MACD", _macd_check(df["Close"], direction)),
        ("support", "Support", _support_check(df, direction, pattern_bars)),
    ]
    checks = []
    score = 0
    total = 0
    for check_id, label, (passed, detail) in specs:
        checks.append({
            "id": check_id,
            "label": label,
            "passed": passed,
            "detail": detail,
        })
        if passed is None:
            continue
        total += 1
        if passed:
            score += 1
    return {"score": score, "total": total, "checks": checks}
