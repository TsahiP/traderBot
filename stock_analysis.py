"""Institutional 8-Dimension Stock Analysis and Screening Engine.

Evaluates US Equities across 8 key quantitative & fundamental dimensions:
1. Earnings Surprise (EPS actual vs estimate, beats/misses)
2. Fundamentals (P/E, Forward P/E, PEG, Profit Margins, ROE, Debt/Equity, Revenue Growth)
3. Analyst Sentiment (Consensus rating, price target implied upside, analyst coverage)
4. Historical Patterns & Realized Volatility (30d/90d annualized volatility, price stability)
5. Market Context (SPY/QQQ trends vs 50/200 SMA, VIX volatility level)
6. Sector Performance (Sector ETF mapping, 30d relative strength alpha)
7. Momentum (RSI-14, 52-week range position, 20/50 SMA momentum, relative volume)
8. Sentiment & Risk Flags (Short interest % float, Safe-Haven risk-off, Overbought/Oversold, Pre-Earnings)
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import logging
import math
import time
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger("tradebot_stock_analysis")
NY = ZoneInfo("America/New_York")

# In-memory cache: {cache_key: (data, timestamp)}
_CACHE: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 120.0  # 2 minutes for individual ticker
MARKET_CACHE_TTL = 300.0  # 5 minutes for macro benchmarks

SECTOR_ETFS = {
    "Technology": "XLK",
    "Information Technology": "XLK",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Discretionary": "XLY",
    "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Materials": "XLB",
    "Communication Services": "XLC",
    "Real Estate": "VNQ",
}

BASE_WEIGHTS = {
    "earnings_surprise": 0.30,
    "fundamentals": 0.20,
    "analyst_sentiment": 0.20,
    "historical_patterns": 0.10,
    "market_context": 0.10,
    "sector_performance": 0.15,
    "momentum": 0.15,
    "sentiment_risk": 0.10,
}


@dataclass
class DimensionScore:
    name: str
    weight: float
    score: float | None
    normalized_weight: float
    summary: str
    metrics: dict


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
    """Compute RSI using standard Wilder smoothing."""
    if len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    gain.iloc[0] = 0.0
    loss.iloc[0] = 0.0

    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    # Wilder exponential moving average smoothing
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


def get_market_context() -> dict:
    """Fetch and cache SPY, QQQ, and VIX status."""
    now = time.time()
    cached = _CACHE.get("__MARKET_CONTEXT__")
    if cached and (now - cached[1]) < MARKET_CACHE_TTL:
        return cached[0]

    market_data = {
        "vix": 18.0,
        "spy_trend": "neutral",
        "qqq_trend": "neutral",
        "market_regime": "Normal",
        "safe_haven_rally": False,
    }

    try:
        vix_t = yf.Ticker("^VIX").history(period="5d")
        if not vix_t.empty:
            market_data["vix"] = _safe_float(vix_t["Close"].iloc[-1], 18.0)
    except Exception as exc:
        logger.debug("VIX fetch note: %s", exc)

    try:
        spy_hist = yf.Ticker("SPY").history(period="1y")
        if len(spy_hist) >= 200:
            c = spy_hist["Close"].iloc[-1]
            sma50 = spy_hist["Close"].rolling(50).mean().iloc[-1]
            sma200 = spy_hist["Close"].rolling(200).mean().iloc[-1]
            if c > sma50 > sma200:
                market_data["spy_trend"] = "bullish"
            elif c < sma50 < sma200:
                market_data["spy_trend"] = "bearish"
            else:
                market_data["spy_trend"] = "neutral"
    except Exception as exc:
        logger.debug("SPY trend note: %s", exc)

    # Safe-haven flight check (GLD, TLT, UUP simultaneous 5d gains)
    try:
        gld = yf.Ticker("GLD").history(period="5d")
        tlt = yf.Ticker("TLT").history(period="5d")
        uup = yf.Ticker("UUP").history(period="5d")
        if len(gld) >= 2 and len(tlt) >= 2 and len(uup) >= 2:
            gld_chg = (gld["Close"].iloc[-1] / gld["Close"].iloc[0]) - 1
            tlt_chg = (tlt["Close"].iloc[-1] / tlt["Close"].iloc[0]) - 1
            uup_chg = (uup["Close"].iloc[-1] / uup["Close"].iloc[0]) - 1
            if gld_chg > 0.015 and tlt_chg > 0.01 and uup_chg > 0.008:
                market_data["safe_haven_rally"] = True
    except Exception:
        pass

    vix = market_data["vix"]
    if vix > 30:
        market_data["market_regime"] = "High Volatility / Fear"
    elif vix > 20:
        market_data["market_regime"] = "Elevated Volatility"
    elif vix < 14:
        market_data["market_regime"] = "Low Volatility / Complacency"
    else:
        market_data["market_regime"] = "Normal"

    _CACHE["__MARKET_CONTEXT__"] = (market_data, now)
    return market_data


def analyze_earnings_surprise(ticker_obj: yf.Ticker, info: dict) -> tuple[float | None, str, dict]:
    """1. Earnings Surprise (30% nominal weight)"""
    metrics = {}
    try:
        earnings_dates = ticker_obj.get_earnings_dates(limit=6)
    except Exception:
        earnings_dates = None

    if earnings_dates is None or earnings_dates.empty:
        # Check if info has trailing eps
        eps = _safe_float(info.get("trailingEps"))
        if eps is not None:
            score = 65.0 if eps > 0 else 35.0
            return score, f"EPS: ${eps:.2f} (No detailed quarterly surprise history)", {"trailing_eps": eps}
        return None, "No earnings surprise data available", {}

    # Calculate average surprise % and beat frequency
    surprises = []
    actuals = []
    estimates = []
    for _, row in earnings_dates.iterrows():
        act = _safe_float(row.get("Reported EPS"))
        est = _safe_float(row.get("EPS Estimate"))
        surp = _safe_float(row.get("Surprise(%)"))
        if act is not None and est is not None:
            actuals.append(act)
            estimates.append(est)
            if surp is not None:
                surprises.append(surp)
            elif est != 0:
                surprises.append(((act - est) / abs(est)) * 100)

    if not surprises:
        return None, "No verified quarterly estimates found", {}

    recent_beats = sum(1 for s in surprises[:4] if s > 0)
    total_q = min(len(surprises), 4)
    avg_surprise = float(np.mean(surprises[:4]))
    latest_actual = actuals[0] if actuals else None
    latest_est = estimates[0] if estimates else None
    latest_surp = surprises[0] if surprises else 0.0

    metrics = {
        "latest_actual_eps": latest_actual,
        "latest_estimate_eps": latest_est,
        "latest_surprise_pct": round(latest_surp, 2),
        "recent_beat_rate": round(recent_beats / total_q, 2),
        "avg_surprise_4q": round(avg_surprise, 2),
    }

    # Score calculation
    base_score = 50.0
    beat_ratio = recent_beats / total_q
    base_score += (beat_ratio - 0.5) * 40.0  # +/- 20 pts based on beat rate
    if avg_surprise > 10.0:
        base_score += 20.0
    elif avg_surprise > 0:
        base_score += 10.0
    elif avg_surprise < -10.0:
        base_score -= 20.0
    else:
        base_score -= 10.0

    score = float(np.clip(base_score, 10.0, 95.0))
    summary = f"Beats {recent_beats}/{total_q} recent quarters with avg surprise {avg_surprise:+.1f}% (Latest: {latest_surp:+.1f}%)"
    return score, summary, metrics


def analyze_fundamentals(info: dict) -> tuple[float | None, str, dict]:
    """2. Fundamental Valuation & Quality (20% nominal weight)"""
    pe = _safe_float(info.get("trailingPE"))
    fwd_pe = _safe_float(info.get("forwardPE"))
    peg = _safe_float(info.get("pegRatio"))
    profit_margin = _safe_float(info.get("profitMargins"))
    operating_margin = _safe_float(info.get("operatingMargins"))
    roe = _safe_float(info.get("returnOnEquity"))
    debt_to_equity = _safe_float(info.get("debtToEquity"))
    rev_growth = _safe_float(info.get("revenueGrowth"))

    metrics = {
        "pe": pe,
        "forward_pe": fwd_pe,
        "peg": peg,
        "profit_margin_pct": round(profit_margin * 100, 2) if profit_margin is not None else None,
        "operating_margin_pct": round(operating_margin * 100, 2) if operating_margin is not None else None,
        "roe_pct": round(roe * 100, 2) if roe is not None else None,
        "debt_to_equity": debt_to_equity,
        "revenue_growth_pct": round(rev_growth * 100, 2) if rev_growth is not None else None,
    }

    valid_count = sum(1 for v in [pe, fwd_pe, profit_margin, roe, rev_growth] if v is not None)
    if valid_count < 2:
        return None, "Insufficient fundamental metrics (ETF or unrated entity)", metrics

    score = 50.0

    # Valuation scoring
    if pe is not None:
        if 5 < pe < 22:
            score += 15.0
        elif pe <= 5 or pe > 45:
            score -= 10.0
    if fwd_pe is not None and pe is not None and fwd_pe < pe:
        score += 8.0  # Expanding earnings expectation

    # PEG Ratio
    if peg is not None:
        if 0.5 <= peg <= 1.5:
            score += 12.0
        elif peg > 2.5:
            score -= 8.0

    # Profitability & Growth
    if profit_margin is not None:
        if profit_margin > 0.15:
            score += 10.0
        elif profit_margin < 0:
            score -= 15.0

    if roe is not None:
        if roe > 0.15:
            score += 8.0
        elif roe < 0:
            score -= 8.0

    if rev_growth is not None:
        if rev_growth > 0.10:
            score += 10.0
        elif rev_growth < -0.05:
            score -= 10.0

    score = float(np.clip(score, 10.0, 95.0))
    summary_parts = []
    if pe is not None:
        summary_parts.append(f"P/E {pe:.1f}")
    if profit_margin is not None:
        summary_parts.append(f"Net Margin {profit_margin*100:.1f}%")
    if rev_growth is not None:
        summary_parts.append(f"Rev Growth {rev_growth*100:+.1f}%")

    return score, " | ".join(summary_parts) or "Fundamentals evaluated", metrics


def analyze_analyst_sentiment(info: dict, current_price: float) -> tuple[float | None, str, dict]:
    """3. Analyst Sentiment & Target Upside (20% nominal weight)"""
    target_mean = _safe_float(info.get("targetMeanPrice"))
    target_high = _safe_float(info.get("targetHighPrice"))
    target_low = _safe_float(info.get("targetLowPrice"))
    consensus = info.get("recommendationKey", "").lower()
    num_analysts = _safe_float(info.get("numberOfAnalystOpinions"), 0)

    metrics = {
        "target_mean": target_mean,
        "target_high": target_high,
        "target_low": target_low,
        "consensus": consensus.upper() if consensus else "N/A",
        "analyst_count": int(num_analysts) if num_analysts else 0,
        "implied_upside_pct": None,
    }

    if not target_mean or current_price <= 0:
        return None, "No analyst price targets available", metrics

    upside_pct = ((target_mean - current_price) / current_price) * 100
    metrics["implied_upside_pct"] = round(upside_pct, 2)

    score = 50.0
    # Upside component
    if upside_pct > 25.0:
        score += 25.0
    elif upside_pct > 10.0:
        score += 15.0
    elif upside_pct < -10.0:
        score -= 20.0
    elif upside_pct < 0:
        score -= 10.0

    # Rating keyword
    if "strong_buy" in consensus:
        score += 15.0
    elif "buy" in consensus:
        score += 8.0
    elif "underperform" in consensus or "sell" in consensus:
        score -= 15.0

    score = float(np.clip(score, 10.0, 95.0))
    summary = f"Consensus {consensus.upper() or 'N/A'} with ${target_mean:.2f} mean target ({upside_pct:+.1f}% upside, {int(num_analysts)} analysts)"
    return score, summary, metrics


def analyze_historical_patterns(hist: pd.DataFrame) -> tuple[float | None, str, dict]:
    """4. Historical Patterns & Realized Volatility (10% nominal weight)"""
    if len(hist) < 30:
        return None, "Insufficient historical price bars", {}

    returns = hist["Close"].pct_change().dropna()
    vol_30d = float(returns.iloc[-30:].std() * np.sqrt(252) * 100)
    vol_90d = float(returns.iloc[-90:].std() * np.sqrt(252) * 100) if len(returns) >= 90 else vol_30d

    # Measure max drawdown over past 6 months / 120 bars
    window_closes = hist["Close"].iloc[-120:] if len(hist) >= 120 else hist["Close"]
    cum_max = window_closes.cummax()
    dd_series = (window_closes - cum_max) / cum_max
    max_dd_pct = float(dd_series.min() * 100)

    metrics = {
        "realized_vol_30d_pct": round(vol_30d, 2),
        "realized_vol_90d_pct": round(vol_90d, 2),
        "max_drawdown_6m_pct": round(max_dd_pct, 2),
    }

    # Lower volatility / moderate steady trend scores higher
    score = 55.0
    if vol_30d < 20.0:
        score += 18.0
    elif vol_30d < 35.0:
        score += 8.0
    elif vol_30d > 65.0:
        score -= 20.0
    elif vol_30d > 45.0:
        score -= 10.0

    if max_dd_pct > -15.0:
        score += 12.0
    elif max_dd_pct < -35.0:
        score -= 15.0

    score = float(np.clip(score, 15.0, 90.0))
    summary = f"30d Realized Vol: {vol_30d:.1f}% | 6m Max Drawdown: {max_dd_pct:.1f}%"
    return score, summary, metrics


def analyze_market_context(market_ctx: dict) -> tuple[float, str, dict]:
    """5. Market Context & Macro Regime (10% nominal weight)"""
    vix = market_ctx.get("vix", 18.0)
    spy_trend = market_ctx.get("spy_trend", "neutral")
    safe_haven = market_ctx.get("safe_haven_rally", False)

    score = 50.0
    if spy_trend == "bullish":
        score += 20.0
    elif spy_trend == "bearish":
        score -= 20.0

    if vix < 15.0:
        score += 15.0
    elif vix > 30.0:
        score -= 25.0
    elif vix > 22.0:
        score -= 10.0

    if safe_haven:
        score -= 15.0

    score = float(np.clip(score, 10.0, 90.0))
    summary = f"Market: {market_ctx.get('market_regime', 'Normal')} (VIX {vix:.1f}, SPY {spy_trend})"
    return score, summary, market_ctx


def analyze_sector_performance(info: dict, hist: pd.DataFrame) -> tuple[float | None, str, dict]:
    """6. Sector Performance & Relative Strength Alpha (15% nominal weight)"""
    sector = info.get("sector", "") or info.get("category", "")
    etf_sym = SECTOR_ETFS.get(sector, "SPY")

    if len(hist) < 22:
        return None, "Not enough history for sector relative strength", {}

    stock_return_30d = float(((hist["Close"].iloc[-1] / hist["Close"].iloc[-22]) - 1) * 100)

    try:
        etf_hist = yf.Ticker(etf_sym).history(period="2mo")
        if len(etf_hist) >= 22:
            etf_return_30d = float(((etf_hist["Close"].iloc[-1] / etf_hist["Close"].iloc[-22]) - 1) * 100)
            alpha_30d = stock_return_30d - etf_return_30d
        else:
            etf_return_30d = 0.0
            alpha_30d = 0.0
    except Exception:
        etf_return_30d = 0.0
        alpha_30d = 0.0

    metrics = {
        "sector": sector or "General Market",
        "benchmark_etf": etf_sym,
        "stock_30d_return_pct": round(stock_return_30d, 2),
        "sector_30d_return_pct": round(etf_return_30d, 2),
        "alpha_30d_pct": round(alpha_30d, 2),
    }

    score = 50.0
    if alpha_30d > 8.0:
        score += 25.0
    elif alpha_30d > 2.0:
        score += 15.0
    elif alpha_30d < -8.0:
        score -= 20.0
    elif alpha_30d < -2.0:
        score -= 10.0

    if etf_return_30d > 3.0:
        score += 10.0
    elif etf_return_30d < -3.0:
        score -= 10.0

    score = float(np.clip(score, 10.0, 95.0))
    summary = f"Sector ({sector or etf_sym}): 30d Alpha {alpha_30d:+.1f}% vs {etf_sym} ({etf_return_30d:+.1f}%)"
    return score, summary, metrics


def analyze_momentum(hist: pd.DataFrame) -> tuple[float, str, dict]:
    """7. Momentum, RSI & Technical Trend (15% nominal weight)"""
    if len(hist) < 15:
        return 50.0, "Insufficient bars for technical momentum", {}

    c = hist["Close"]
    current_price = float(c.iloc[-1])
    rsi = compute_rsi(c, 14)

    # 52w range position
    high_52w = float(c.iloc[-252:].max()) if len(c) >= 252 else float(c.max())
    low_52w = float(c.iloc[-252:].min()) if len(c) >= 252 else float(c.min())
    range_pos = (
        ((current_price - low_52w) / (high_52w - low_52w) * 100) if high_52w > low_52w else 50.0
    )

    sma20 = float(c.rolling(20).mean().iloc[-1]) if len(c) >= 20 else current_price
    sma50 = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else current_price

    vol = hist["Volume"]
    vol_20d_avg = float(vol.rolling(20).mean().iloc[-1]) if len(vol) >= 20 else float(vol.mean())
    rel_vol = float(vol.iloc[-1] / vol_20d_avg) if vol_20d_avg > 0 else 1.0

    metrics = {
        "rsi_14": rsi,
        "range_52w_position_pct": round(range_pos, 1),
        "price_vs_sma20_pct": round(((current_price / sma20) - 1) * 100, 2),
        "price_vs_sma50_pct": round(((current_price / sma50) - 1) * 100, 2),
        "relative_volume": round(rel_vol, 2),
    }

    score = 50.0
    # RSI sweet spots
    if 45 <= rsi <= 65:
        score += 15.0  # Healthy upward momentum
    elif 30 <= rsi < 45:
        score += 5.0
    elif rsi > 75:
        score -= 10.0  # Overbought penalty
    elif rsi < 25:
        score -= 5.0  # Deep oversold / falling knife

    # Moving average trend
    if current_price > sma20 > sma50:
        score += 20.0
    elif current_price < sma20 < sma50:
        score -= 20.0

    # Range breakout
    if range_pos > 75:
        score += 8.0
    elif range_pos < 25:
        score -= 8.0

    score = float(np.clip(score, 10.0, 95.0))
    trend_desc = "Bullish" if current_price > sma50 else "Bearish"
    summary = f"RSI(14): {rsi:.1f} | 52w Range: {range_pos:.0f}% | Trend: {trend_desc} (Rel Vol: {rel_vol:.1f}x)"
    return score, summary, metrics


def analyze_sentiment_and_risk(
    info: dict, hist: pd.DataFrame, current_price: float, market_ctx: dict
) -> tuple[float, list[str], dict]:
    """8. Sentiment & Risk Flags (10% nominal weight)"""
    flags = []
    short_pct = _safe_float(info.get("shortPercentOfFloat"))
    shares_short = _safe_float(info.get("sharesShort"))

    # Check earnings proximity
    earnings_dt_raw = info.get("earningsTimestamp") or info.get("earningsDate")
    if earnings_dt_raw:
        try:
            if isinstance(earnings_dt_raw, list) and earnings_dt_raw:
                dt_val = earnings_dt_raw[0]
            else:
                dt_val = earnings_dt_raw
            if isinstance(dt_val, (int, float)):
                earnings_date = datetime.fromtimestamp(dt_val, tz=NY).date()
            else:
                earnings_date = pd.to_datetime(dt_val).date()

            days_to_earnings = (earnings_date - datetime.now(NY).date()).days
            if 0 <= days_to_earnings <= 14:
                flags.append(f"Pre-earnings warning: earnings report in {days_to_earnings} days (recommend caution)")
        except Exception:
            pass

    # Post earnings spike check
    if len(hist) >= 6:
        five_day_chg = ((hist["Close"].iloc[-1] / hist["Close"].iloc[-6]) - 1) * 100
        if five_day_chg > 15.0:
            flags.append(f"Post-earnings/momentum spike: +{five_day_chg:.1f}% in 5 days (gains may be priced in)")

    # Overbought check
    if len(hist) >= 15:
        rsi = compute_rsi(hist["Close"], 14)
        if rsi > 70.0:
            flags.append(f"Overbought condition: RSI at {rsi:.1f}")
        elif rsi < 30.0:
            flags.append(f"Oversold condition: RSI at {rsi:.1f}")

    if market_ctx.get("vix", 0) > 30.0:
        flags.append("High Market Volatility: VIX > 30 creates headwind for long equities")

    if market_ctx.get("safe_haven_rally"):
        flags.append("Risk-Off Flight: Gold, Treasuries, and USD surging together")

    metrics = {
        "short_percent_of_float": round(short_pct * 100, 2) if short_pct is not None else None,
        "shares_short": shares_short,
        "risk_flags_count": len(flags),
    }

    score = 55.0
    if short_pct is not None:
        if short_pct > 0.15:
            score -= 10.0  # High short conviction
        elif short_pct < 0.03:
            score += 10.0

    # Subtract for risk flags
    score -= len(flags) * 8.0
    score = float(np.clip(score, 10.0, 90.0))
    return score, flags, metrics


def analyze_stock(symbol: str) -> dict:
    """Run full institutional 8-dimension quantitative analysis for a stock symbol."""
    sym = symbol.strip().upper()
    now = time.time()
    cached = _CACHE.get(sym)
    if cached and (now - cached[1]) < CACHE_TTL:
        return cached[0]

    ticker_obj = yf.Ticker(sym)
    try:
        hist = ticker_obj.history(period="1y")
    except Exception as exc:
        logger.warning("History fetch error for %s: %s", sym, exc)
        hist = pd.DataFrame()

    if hist.empty:
        raise ValueError(f"No price history available for '{sym}' - check symbol validity")

    try:
        info = ticker_obj.info or {}
    except Exception:
        info = {}

    current_price = _safe_float(hist["Close"].iloc[-1], 0.0)
    market_ctx = get_market_context()

    # 1. Earnings Surprise
    es_score, es_summary, es_metrics = analyze_earnings_surprise(ticker_obj, info)
    # 2. Fundamentals
    fd_score, fd_summary, fd_metrics = analyze_fundamentals(info)
    # 3. Analyst Sentiment
    as_score, as_summary, as_metrics = analyze_analyst_sentiment(info, current_price)
    # 4. Historical Patterns
    hp_score, hp_summary, hp_metrics = analyze_historical_patterns(hist)
    # 5. Market Context
    mc_score, mc_summary, mc_metrics = analyze_market_context(market_ctx)
    # 6. Sector Performance
    sp_score, sp_summary, sp_metrics = analyze_sector_performance(info, hist)
    # 7. Momentum
    mm_score, mm_summary, mm_metrics = analyze_momentum(hist)
    # 8. Sentiment & Risk
    sr_score, risk_flags, sr_metrics = analyze_sentiment_and_risk(info, hist, current_price, market_ctx)

    raw_dimensions = [
        ("earnings_surprise", "Earnings Surprise", BASE_WEIGHTS["earnings_surprise"], es_score, es_summary, es_metrics),
        ("fundamentals", "Fundamentals & Valuation", BASE_WEIGHTS["fundamentals"], fd_score, fd_summary, fd_metrics),
        ("analyst_sentiment", "Analyst Sentiment", BASE_WEIGHTS["analyst_sentiment"], as_score, as_summary, as_metrics),
        ("historical_patterns", "Historical Volatility", BASE_WEIGHTS["historical_patterns"], hp_score, hp_summary, hp_metrics),
        ("market_context", "Market Context", BASE_WEIGHTS["market_context"], mc_score, mc_summary, mc_metrics),
        ("sector_performance", "Sector Relative Strength", BASE_WEIGHTS["sector_performance"], sp_score, sp_summary, sp_metrics),
        ("momentum", "Momentum & Trend", BASE_WEIGHTS["momentum"], mm_score, mm_summary, mm_metrics),
        ("sentiment_risk", "Sentiment & Risk", BASE_WEIGHTS["sentiment_risk"], sr_score, "Risk flag evaluation", sr_metrics),
    ]

    # Dynamically normalize weights across valid dimensions
    active_weight_sum = sum(w for _, _, w, score, _, _ in raw_dimensions if score is not None)
    if active_weight_sum <= 0:
        active_weight_sum = 1.0

    dimensions = []
    composite_score = 0.0
    for dim_id, label, weight, score, summary, metrics in raw_dimensions:
        if score is not None:
            normalized_weight = weight / active_weight_sum
            composite_score += score * normalized_weight
        else:
            normalized_weight = 0.0

        dimensions.append({
            "id": dim_id,
            "label": label,
            "base_weight": weight,
            "normalized_weight": round(normalized_weight, 3),
            "score": round(score, 1) if score is not None else None,
            "summary": summary,
            "metrics": metrics,
        })

    composite_score = float(np.clip(composite_score, 0.0, 100.0))

    # Determine institutional action recommendation
    if composite_score >= 75.0:
        recommendation = "STRONG BUY" if not risk_flags else "BUY"
    elif composite_score >= 60.0:
        recommendation = "BUY"
    elif composite_score >= 42.0:
        recommendation = "HOLD"
    elif composite_score >= 28.0:
        recommendation = "SELL"
    else:
        recommendation = "STRONG SELL"

    confidence = round(max(40.0, min(95.0, (active_weight_sum / sum(BASE_WEIGHTS.values())) * 100 - len(risk_flags) * 5)), 1)

    result = {
        "symbol": sym,
        "company_name": info.get("shortName") or info.get("longName") or sym,
        "current_price": round(current_price, 2),
        "currency": info.get("currency", "USD"),
        "overall_score": round(composite_score, 1),
        "recommendation": recommendation,
        "confidence_pct": confidence,
        "risk_flags": risk_flags,
        "dimensions": dimensions,
        "timestamp": datetime.now(NY).isoformat(timespec="seconds"),
    }

    _CACHE[sym] = (result, now)
    return result
