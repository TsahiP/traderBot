"""Crypto Top-20 Market Monitor & Quantitative Analytics Engine.

Provides real-time tracking, 30-day rolling BTC correlation, annualized volatility
regimes, momentum metrics, and composite scoring across top cryptocurrencies.
"""
from dataclasses import dataclass
from datetime import datetime
import logging
import math
import time
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger("tradebot_crypto_analysis")
NY = ZoneInfo("America/New_York")

TOP_20_CRYPTOS = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD",
    "ADA-USD", "DOGE-USD", "AVAX-USD", "DOT-USD", "LINK-USD",
    "ATOM-USD", "UNI-USD", "LTC-USD", "BCH-USD", "XLM-USD",
    "ALGO-USD", "VET-USD", "FIL-USD", "NEAR-USD", "TRX-USD",
]

CRYPTO_CATEGORIES = {
    "BTC-USD": ("Bitcoin", "Store of Value"),
    "ETH-USD": ("Ethereum", "Smart Contract L1"),
    "BNB-USD": ("BNB", "Exchange Token"),
    "SOL-USD": ("Solana", "Smart Contract L1"),
    "XRP-USD": ("XRP", "Payment / Settlement"),
    "ADA-USD": ("Cardano", "Smart Contract L1"),
    "DOGE-USD": ("Dogecoin", "Meme / PoW"),
    "AVAX-USD": ("Avalanche", "Smart Contract L1"),
    "DOT-USD": ("Polkadot", "Interoperability"),
    "LINK-USD": ("Chainlink", "Oracle / Web3 Infra"),
    "ATOM-USD": ("Cosmos", "Interoperability"),
    "UNI-USD": ("Uniswap", "DeFi / DEX"),
    "LTC-USD": ("Litecoin", "Payment"),
    "BCH-USD": ("Bitcoin Cash", "Payment"),
    "XLM-USD": ("Stellar", "Payment"),
    "ALGO-USD": ("Algorand", "Smart Contract L1"),
    "VET-USD": ("VeChain", "Enterprise Supply"),
    "FIL-USD": ("Filecoin", "Decentralized Storage"),
    "NEAR-USD": ("NEAR Protocol", "Smart Contract L1"),
    "TRX-USD": ("TRON", "Smart Contract L1"),
}

_CRYPTO_CACHE: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 60.0  # 60 seconds TTL


def _safe_float(val, default: float | None = None) -> float | None:
    if val is None or pd.isna(val):
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def compute_rsi(series: pd.Series, period: int = 14) -> float:
    """Wilder RSI calculation."""
    if len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    gain.iloc[0] = 0.0
    loss.iloc[0] = 0.0

    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    for i in range(period, len(series)):
        prev_g = avg_gain.iloc[i - 1]
        prev_l = avg_loss.iloc[i - 1]
        if pd.isna(prev_g) or pd.isna(prev_l):
            continue
        avg_gain.iloc[i] = (prev_g * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (prev_l * (period - 1) + loss.iloc[i]) / period

    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return 50.0
    if last_loss == 0.0:
        return 100.0 if last_gain > 0.0 else 50.0
    if last_gain == 0.0:
        return 0.0
    rs = last_gain / last_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def get_btc_returns(period: str = "6mo") -> pd.Series:
    """Fetch and cache BTC-USD daily returns for rolling correlation."""
    now = time.time()
    cached = _CRYPTO_CACHE.get("__BTC_RETURNS__")
    if cached and (now - cached[1]) < CACHE_TTL:
        return cached[0]["returns"]

    try:
        btc_hist = yf.Ticker("BTC-USD").history(period=period)
        if not btc_hist.empty:
            returns = btc_hist["Close"].pct_change().dropna()
            _CRYPTO_CACHE["__BTC_RETURNS__"] = ({"returns": returns}, now)
            return returns
    except Exception as exc:
        logger.warning("Error fetching BTC returns: %s", exc)

    return pd.Series(dtype=float)


def classify_volatility_regime(annualized_vol: float) -> tuple[str, str]:
    """Classify 30d annualized volatility into market regimes."""
    if annualized_vol < 40.0:
        return "Low Volatility", "Steady / Rangebound"
    elif annualized_vol < 70.0:
        return "Moderate Volatility", "Normal Crypto Fluctuations"
    elif annualized_vol < 100.0:
        return "High Volatility", "Elevated Risk / Breakout Phase"
    else:
        return "Extreme Volatility", "Severe Dislocation / High Risk"


def compute_btc_correlation(asset_returns: pd.Series, btc_returns: pd.Series, window: int = 30) -> float:
    """Compute 30-day Pearson correlation of daily returns with Bitcoin."""
    if asset_returns.empty or btc_returns.empty:
        return 1.0

    aligned = pd.concat([asset_returns, btc_returns], axis=1, join="inner").dropna()
    if len(aligned) < 10:
        return 1.0

    tail = aligned.iloc[-window:]
    corr = tail.iloc[:, 0].corr(tail.iloc[:, 1])
    if pd.isna(corr) or math.isnan(corr) or math.isinf(corr):
        return 1.0
    return round(float(corr), 3)


def analyze_single_crypto(symbol: str, hist: pd.DataFrame | None = None, info: dict | None = None, btc_returns: pd.Series | None = None) -> dict:
    """Analyze single crypto asset with momentum, volatility, and BTC correlation."""
    sym = symbol.strip().upper()
    if not sym.endswith("-USD") and not "-" in sym:
        sym = f"{sym}-USD"

    name, category = CRYPTO_CATEGORIES.get(sym, (sym.replace("-USD", ""), "Cryptocurrency"))

    ticker_obj = yf.Ticker(sym)
    if hist is None or hist.empty:
        try:
            hist = ticker_obj.history(period="6mo")
        except Exception as exc:
            logger.warning("History fetch error for %s: %s", sym, exc)
            hist = pd.DataFrame()

    if hist.empty or len(hist) < 5:
        raise ValueError(f"No price data available for crypto asset '{sym}'")

    if info is None:
        try:
            info = ticker_obj.info or {}
        except Exception:
            info = {}

    if btc_returns is None:
        btc_returns = get_btc_returns()

    closes = hist["Close"]
    current_price = _safe_float(closes.iloc[-1], 0.0)
    daily_returns = closes.pct_change().dropna()

    # Returns over 24h, 7d, 30d
    chg_24h = float(((current_price / closes.iloc[-2]) - 1) * 100) if len(closes) >= 2 else 0.0
    chg_7d = float(((current_price / closes.iloc[-7]) - 1) * 100) if len(closes) >= 7 else 0.0
    chg_30d = float(((current_price / closes.iloc[-30]) - 1) * 100) if len(closes) >= 30 else 0.0

    # 30-day annualized realized volatility (crypto trades 365 days/year)
    vol_window = daily_returns.iloc[-30:] if len(daily_returns) >= 30 else daily_returns
    vol_30d = float(vol_window.std() * np.sqrt(365) * 100) if len(vol_window) > 2 else 50.0
    vol_regime, vol_desc = classify_volatility_regime(vol_30d)

    # 30-day BTC correlation
    if sym == "BTC-USD":
        btc_corr = 1.0
    else:
        btc_corr = compute_btc_correlation(daily_returns, btc_returns, window=30)

    # Technical momentum & RSI
    rsi = compute_rsi(closes, 14)
    sma20 = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else current_price
    sma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else current_price

    high_52w = float(closes.max())
    low_52w = float(closes.min())
    range_pos = (((current_price - low_52w) / (high_52w - low_52w) * 100) if high_52w > low_52w else 50.0)

    market_cap = _safe_float(info.get("marketCap"))
    volume_24h = _safe_float(info.get("volume24Hr") or info.get("totalVolume") or hist["Volume"].iloc[-1] * current_price)

    # Classification by market cap
    if market_cap:
        if market_cap >= 100_000_000_000:
            cap_class = "Mega Cap"
        elif market_cap >= 10_000_000_000:
            cap_class = "Large Cap"
        elif market_cap >= 1_000_000_000:
            cap_class = "Mid Cap"
        else:
            cap_class = "Small Cap"
    else:
        cap_class = "Unclassified"

    # Composite Scoring (0 - 100)
    score = 50.0
    # Momentum component
    if 45 <= rsi <= 68:
        score += 15.0
    elif rsi > 75:
        score -= 10.0  # Overbought
    elif rsi < 30:
        score += 5.0  # Oversold bounce candidate

    # Trend component
    if current_price > sma20 > sma50:
        score += 20.0
    elif current_price < sma20 < sma50:
        score -= 20.0

    # 30d return contribution
    if chg_30d > 15.0:
        score += 10.0
    elif chg_30d < -15.0:
        score -= 10.0

    # Volatility penalty
    if vol_30d > 100.0:
        score -= 10.0
    elif vol_30d < 45.0:
        score += 5.0

    score = float(np.clip(score, 10.0, 95.0))

    if score >= 75.0:
        signal = "STRONG BUY"
    elif score >= 60.0:
        signal = "BUY"
    elif score >= 42.0:
        signal = "HOLD"
    elif score >= 28.0:
        signal = "SELL"
    else:
        signal = "STRONG SELL"

    return {
        "symbol": sym,
        "name": name,
        "category": category,
        "current_price": round(current_price, 4 if current_price < 1.0 else 2),
        "market_cap": market_cap,
        "market_cap_class": cap_class,
        "volume_24h": volume_24h,
        "change_24h_pct": round(chg_24h, 2),
        "change_7d_pct": round(chg_7d, 2),
        "change_30d_pct": round(chg_30d, 2),
        "btc_correlation_30d": btc_corr,
        "realized_vol_30d_pct": round(vol_30d, 1),
        "volatility_regime": vol_regime,
        "volatility_description": vol_desc,
        "rsi_14": rsi,
        "price_vs_sma20_pct": round(((current_price / sma20) - 1) * 100, 2),
        "price_vs_sma50_pct": round(((current_price / sma50) - 1) * 100, 2),
        "range_52w_position_pct": round(range_pos, 1),
        "composite_score": round(score, 1),
        "signal": signal,
    }


def get_crypto_top20_overview() -> dict:
    """Fetch, analyze and rank top 20 crypto assets."""
    now = time.time()
    cached = _CRYPTO_CACHE.get("__TOP20_OVERVIEW__")
    if cached and (now - cached[1]) < CACHE_TTL:
        return cached[0]

    btc_returns = get_btc_returns()
    assets = []

    # Batch download to minimize network roundtrips
    try:
        symbols_str = " ".join(TOP_20_CRYPTOS)
        batch_data = yf.download(symbols_str, period="3mo", group_by="ticker", progress=False, threads=True)
    except Exception as exc:
        logger.warning("Batch crypto download failed, falling back to sequential: %s", exc)
        batch_data = None

    for sym in TOP_20_CRYPTOS:
        try:
            hist = None
            if batch_data is not None:
                if isinstance(batch_data.columns, pd.MultiIndex) and sym in batch_data.columns.levels[0]:
                    hist = batch_data[sym].dropna(subset=["Close"])
            data = analyze_single_crypto(sym, hist=hist, btc_returns=btc_returns)
            assets.append(data)
        except Exception as exc:
            logger.debug("Failed analyzing %s in top20: %s", sym, exc)

    # Sort primarily by market cap, falling back to volume
    assets.sort(key=lambda a: a.get("market_cap") or a.get("volume_24h") or 0.0, reverse=True)

    result = {
        "assets": assets,
        "count": len(assets),
        "timestamp": datetime.now(NY).isoformat(timespec="seconds"),
    }

    _CRYPTO_CACHE["__TOP20_OVERVIEW__"] = (result, now)
    return result


def analyze_crypto(symbol: str) -> dict:
    """Analyze single crypto asset with caching."""
    sym = symbol.strip().upper()
    if not sym.endswith("-USD") and not "-" in sym:
        sym = f"{sym}-USD"

    now = time.time()
    cached = _CRYPTO_CACHE.get(sym)
    if cached and (now - cached[1]) < CACHE_TTL:
        return cached[0]

    data = analyze_single_crypto(sym)
    _CRYPTO_CACHE[sym] = (data, now)
    return data
