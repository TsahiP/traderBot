"""Analysis context builder for the Discord analyst bot.

Implements the 5-step protocol (Catalysts, Macro, SMC, LazyBear squeeze,
Execution). Every number is computed in Python from data we already pull;
the local LLM only phrases steps 1-4 and the verdict (analyst_steps), one
small call each - Step 5 is rendered in code and it never computes levels.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import pandas as pd

import analyst_steps
import config
import funda_client
import news_client
import signals
import market_data
import market_context
import market_levels
from confirmations import volume_ratio
from llm_client import llm_chat_ex, resolve_llm_model
from smc import snapshot_smc
import squeeze as squeeze_mod
import trade_plan
from analysis_charts import MIN_BARS as CHART_MIN_BARS, render_analysis_chart

# timeframe -> yfinance period (LuxAlgo SMC needs ~250+ bars: swing 50, ATR 200)
TF_SPECS = {
    "1d": {"period": "2y"},
    "15m": {"period": "60d"},
}

ATR_PERIOD = 14
VOL_ABSORB_MULT = 1.5
RS_BENCHMARK = "SPY"
VP_BARS = 60               # daily bars in the step-3 volume profile


def gather_symbol_data(
    symbol: str,
    *,
    refresh: bool = True,
    poll_news: bool = True,
    watch_cfg: dict | None = None,
    holding: dict | None = None,
) -> dict:
    """Collect every source we have for a symbol into one data dict."""
    symbol = symbol.upper()
    if refresh:
        try:
            funda_client.refresh_symbol(symbol, force=False)
        except Exception as exc:  # noqa: BLE001 — refresh failure must not abort
            refresh_error = str(exc)[:120]
        else:
            refresh_error = None
    else:
        refresh_error = None

    snapshot = funda_client.read_snapshot(symbol) or {}
    fields = snapshot.get("fields", {}) if isinstance(snapshot, dict) else {}

    news_error = None
    if poll_news:
        try:
            news_client.poll_symbol(symbol)
        except Exception as exc:  # noqa: BLE001
            news_error = str(exc)[:120]
    raw_news = news_client.read_symbol_news(symbol)
    news = [
        {
            "ts": it.get("ts"),
            "source": it.get("source"),
            "title": (it.get("title") or "")[:140],
            "summary": (it.get("summary") or "")[:200] or None,
            "sentiment": it.get("sentiment"),
            **({"tags": it["tags"]} if it.get("tags") else {}),
        }
        for it in raw_news[: config.ANALYST_NEWS_MAX]
    ]

    tries = signals.read_signals(limit=200)
    matched = [e for e in tries if isinstance(e, dict) and e.get("symbol") == symbol]
    recent_signals = [
        {
            "ts": e.get("ts"),
            "timeframe": e.get("timeframe"),
            "direction": e.get("direction"),
            "label": (e.get("label") or "")[:60],
            "entry": e.get("entry"),
            "stop": e.get("stop"),
            "target": e.get("target"),
            "close": e.get("close"),
        }
        for e in matched[: config.ANALYST_SIGNALS_MAX]
    ]

    watch_cfg = watch_cfg or {}
    in_lists = [
        wl.get("name", "?")
        for wl in watch_cfg.get("lists", [])
        if symbol in [str(s).upper() for s in wl.get("symbols", [])]
    ]

    quarters = [
        {k: q.get(k) for k in ("end", "revenue", "net_income", "gross_profit", "eps", "src",
                               "revenue_yoy_pct", "net_income_yoy_pct", "eps_yoy_pct",
                               "gross_margin_pct", "net_margin_pct") if q.get(k) is not None}
        for q in (snapshot.get("quarters") or [])[-4:]
    ]

    data: dict = {
        "symbol": symbol,
        "asset": snapshot.get("asset", "stock"),
        "updated": snapshot.get("updated"),
        "fields": {k: {"v": f.get("v"), "src": f.get("src")} for k, f in fields.items()},
        "quarters": quarters,
        "news": news,
        "recent_signals": recent_signals,
        "watchlists": in_lists,
        "snapshot_errors": (snapshot.get("errors") or [])[:5],
        "fetch_errors": [e for e in (refresh_error, news_error) if e],
        "holding": holding,
        "charts": {},
    }

    fetch_errors = data["fetch_errors"]

    # ---- step 1 extras: daily price action + ex-dividend
    daily_df = _fetch_bars(symbol, "1d", fetch_errors)
    intraday_df = _fetch_bars(symbol, "15m", fetch_errors)
    data["price_action"] = _safe_step("price_action", lambda: _price_action_daily(daily_df), fetch_errors)
    data["ex_dividend"] = _safe_step("ex_dividend", lambda: _ex_dividend_date(symbol), fetch_errors)

    # ---- step 2: macro (TTL-cached inside market_context) + relative strength vs SPY
    data["macro"] = _safe_step("macro", market_context.market_context, fetch_errors)
    if daily_df is not None and symbol != RS_BENCHMARK:
        bench_df = _fetch_bars(RS_BENCHMARK, "1d", fetch_errors)
        if bench_df is not None:
            data["relative_strength"] = _safe_step(
                "relative_strength", lambda: market_levels.relative_strength(daily_df, bench_df), fetch_errors)

    # ---- step 3 extra: volume profile of the last VP_BARS daily bars
    if daily_df is not None and len(daily_df) >= 20:
        data["volume_profile_daily"] = _safe_step(
            "volume_profile", lambda: market_levels.volume_profile(daily_df.tail(VP_BARS)), fetch_errors)

    # ---- step 3: SMC snapshots per timeframe
    for tf, df in (("daily", daily_df), ("15m", intraday_df)):
        if df is None or len(df) < 8:
            continue
        data[f"smc_{tf}"] = _safe_step(
            f"smc_{tf}", lambda df=df: snapshot_smc(df.reset_index(drop=True)),
            fetch_errors,
        )

    # ---- step 4: squeeze per timeframe
    for tf, df in (("daily", daily_df), ("15m", intraday_df)):
        if df is None or len(df) < squeeze_mod.MIN_BARS:
            continue
        data[f"squeeze_{tf}"] = _safe_step(
            f"squeeze_{tf}", lambda df=df: squeeze_mod.squeeze_snapshot(df.reset_index(drop=True)),
            fetch_errors,
        )

    # ---- step 5: execution plan from the 1D snapshot
    atr = _safe_step("atr", lambda: _atr(daily_df), fetch_errors)
    data["atr_daily"] = atr
    if isinstance(daily_df, pd.DataFrame) and len(daily_df):
        price = float(daily_df["Close"].iloc[-1])
        smc_daily = data.get("smc_daily") or {}
        risk = None
        hl = holding if isinstance(holding, dict) else {}
        try:
            risk = float(hl.get("risk_usd") or config.ANALYST_RISK_USD)
        except (TypeError, ValueError):
            risk = config.ANALYST_RISK_USD
        data["trade_plan"] = _safe_step(
            "trade_plan",
            lambda: trade_plan.build_trade_plan(
                price,
                smc_daily if isinstance(smc_daily, dict) else {},
                atr if isinstance(atr, float) else 0.0,
                risk or 0.0,
                holding={"qty": hl.get("qty"), "avg_price": hl.get("avg_price")}
                if hl.get("qty") and hl.get("avg_price") else None,
                account={"equity": config.ANALYST_ACCOUNT_USD} if config.ANALYST_ACCOUNT_USD > 0 else None,
                invest_usd=hl.get("invest_usd"),
                target_rr=config.ANALYST_TARGET_RR,
            ),
            fetch_errors,
        )

    # ---- chart PNGs for vision + Discord attachment
    data["charts"] = _safe_step(
        "charts",
        lambda: _render_charts(symbol, {"1d": daily_df, "15m": intraday_df}, fetch_errors),
        fetch_errors,
    ) or {}
    return data


def _safe_step(label: str, fn, fetch_errors: list) -> object:
    """Run one step; a failure records the error and yields None."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - one failed step must not abort
        fetch_errors.append(f"{label}: {str(exc)[:90]}")
        return None


def _fetch_bars(symbol: str, tf: str, fetch_errors: list) -> pd.DataFrame | None:
    try:
        spec = TF_SPECS[tf]
        df = market_data.fetch_ohlcv_period(symbol, tf, spec["period"])
    except Exception as exc:  # noqa: BLE001
        fetch_errors.append(f"{tf} bars: {str(exc)[:90]}")
        return None
    return df if isinstance(df, pd.DataFrame) and len(df) else None


def _render_charts(symbol: str, bars: dict, fetch_errors: list) -> dict:
    charts: dict = {}
    for tf, df in bars.items():
        if df is None or len(df) < CHART_MIN_BARS:
            continue
        try:
            charts[tf] = render_analysis_chart(df, symbol, tf)
        except Exception as exc:  # noqa: BLE001
            fetch_errors.append(f"chart {tf}: {str(exc)[:90]}")
    return charts


def _atr(df: pd.DataFrame | None, period: int = ATR_PERIOD) -> float | None:
    if df is None or len(df) < period + 1:
        return None
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    val = float(tr.rolling(period).mean().iloc[-1])
    return val if val == val else None


def _price_action_daily(df: pd.DataFrame | None) -> dict:
    """Step-1 numbers: change, gap, relative volume, close position, read."""
    if df is None or len(df) < 21:
        return {"ok": False, "error": "no daily bars"}
    close = df["Close"].astype(float)
    open_ = df["Open"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
    gap = (float(open_.iloc[-1]) - prev) / prev * 100.0 if prev else None
    change = (last - prev) / prev * 100.0 if prev else None
    vol_ratio = volume_ratio(df["Volume"].astype(float))
    rng = float(high.iloc[-1]) - float(low.iloc[-1])
    close_pos = (last - float(low.iloc[-1])) / rng * 100.0 if rng > 0 else None
    read = None
    if vol_ratio is not None and close_pos is not None and vol_ratio >= VOL_ABSORB_MULT:
        read = "absorption" if close_pos >= 50.0 else "distribution"
    return {
        "ok": True,
        "change_pct": round(change, 2) if change is not None else None,
        "gap_pct": round(gap, 2) if gap is not None else None,
        "volume_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "close_position_pct": round(close_pos, 1) if close_pos is not None else None,
        "read": read,
    }


def _ex_dividend_date(symbol: str) -> str | None:
    """yfinance calendar in one call; date only, n/a quietly on failure."""
    try:
        import yfinance as yf

        raw = yf.Ticker(symbol).calendar
    except Exception:  # noqa: BLE001
        return None
    val = None
    if isinstance(raw, dict):
        val = (raw.get("Ex-Dividend Date") or raw.get("exDividendDate")
               or raw.get("Ex Dividend Date"))
    elif raw is not None and hasattr(raw, "index"):
        try:  # some yfinance versions return a small frame
            row = raw.loc[[v for v in raw.index if "Ex-Dividend" in str(v)]]
            val = row.iloc[0, 0]
        except Exception:  # noqa: BLE001
            val = None
    if val in (None, "N/A", ""):
        return None
    if isinstance(val, (int, float)):  # epoch seconds
        try:
            return datetime.fromtimestamp(float(val)).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if hasattr(val, "date"):
        return val.date().isoformat()
    return str(val)[:10]


def build_context_json(data: dict) -> str:
    """Compact, model-friendly dump: step1..step5 + signals + holding."""
    compact = {
        "symbol": data["symbol"],
        "step1_catalysts": {
            "symbol": data["symbol"],
            "asset": data.get("asset"),
            "updated": data.get("updated"),
            "fields": data.get("fields", {}),
            "last_quarters": data.get("quarters", []),
            "price_action": data.get("price_action"),
            "ex_dividend_date": data.get("ex_dividend"),
            "news": data.get("news", []),
            "snapshot_errors": data.get("snapshot_errors", []),
        },
        "step2_macro": {
            **(data.get("macro") or {}),
            "relative_strength_vs_spy": data.get("relative_strength"),
        } if data.get("macro") or data.get("relative_strength") else None,
        "step3_smc": {
            "daily": data.get("smc_daily"),
            "15m": data.get("smc_15m"),
            "volume_profile_60d": data.get("volume_profile_daily"),
        },
        "step4_squeeze": {
            "daily": data.get("squeeze_daily"),
            "15m": data.get("squeeze_15m"),
        },
        "step5_execution": data.get("trade_plan"),
        "signals": data.get("recent_signals", []),
        "watchlists": data.get("watchlists", []),
        "holding": data.get("holding"),
        "data_problem_notes": data.get("snapshot_errors", []) + data.get("fetch_errors", []),
    }
    return json.dumps(compact, ensure_ascii=False, default=str)


def _analysis_call(messages: list[dict], *, max_tokens: int = 12000) -> str:
    """LLM call with a token-budget ladder.

    Bonsai (qwen35 arch) reasons inside a separate reasoning_content field and
    LM Studio ignores our enable_thinking=False; the think phase can eat most
    of the budget, so the answer arrives empty (finish "length") or cut
    mid-sentence with partial content. Escalate on empty OR finish_reason
    "length" and re-generate (observed ~60 tok/s: 15k tokens ~ 4 min).
    """
    logger = logging.getLogger("analystbot")
    analysis, meta = "", {}
    for attempt in (1, 2, 3):
        analysis, meta = llm_chat_ex(
            messages,
            temperature=0.2,
            max_tokens=max_tokens,
            timeout=config.ANALYST_LLM_TIMEOUT,
            preload=(attempt == 1),
            reasoning_effort=config.ANALYST_REASONING_EFFORT or None,
        )
        finished = meta.get("finish_reason") == "stop"
        if analysis.strip() and finished:
            break
        logger.warning(
            "LLM analysis attempt %d %s (budget %d tokens, finish=%s, reasoning %s) - escalating",
            attempt,
            "empty" if not analysis.strip() else "truncated",
            max_tokens, meta.get("finish_reason"), meta.get("reasoning_tokens"),
        )
        max_tokens = max_tokens * 2 + 3000  # 12000 -> 27000 -> 57000
    if not analysis.strip():
        logger.error("LLM analysis empty after 3 attempts (finish=%s)", meta.get("finish_reason"))
    return analysis


def run_analysis(data: dict) -> tuple[str, str]:
    """Gathered data -> (analysis_markdown, model_id).

    One small LLM call per step (analyst_steps), Step 5 rendered in code. With
    vision on (ANALYST_VISION) the Smart Money and Squeeze steps get the two
    chart PNGs; each falls back to text-only when the model rejects images.
    """
    images = None
    charts = data.get("charts") or {}
    if config.ANALYST_VISION and charts:
        images = [(f"{tf} chart", png) for tf, png in charts.items() if png]
    return analyst_steps.build_card(data, _analysis_call, images), resolve_llm_model()


def _analysis_age_hours(data: dict) -> float | None:
    raw = data.get("updated")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
        now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
        return max(0.0, (now - ts).total_seconds() / 3600.0)
    except (ValueError, TypeError):
        return None


def split_discord(text: str, limit: int = 1900) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.splitlines():
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur.strip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks or [""]


def format_reply(symbol: str, analysis: str, data: dict) -> list[str]:
    """Header + analysis, footer with data age; split into Discord-size chunks."""
    header = f"**🔎 פונדי אנליסט — {symbol}**"
    age = _analysis_age_hours(data)
    stamp = data.get("updated")
    footer_bits = []
    if stamp:
        footer_bits.append(f"סנפשוט {stamp[:16]}")
    if age is not None:
        footer_bits.append(f"לפני {age:.1f} שע'")
    footer = f"_נתונים: {' · '.join(footer_bits)}_" if footer_bits else ""
    body = analysis.strip() or "_empty response_"

    first = f"{header}\n{body}".strip()
    chunks = split_discord(first)
    if footer:
        last = chunks[-1]
        if len(last) + len(footer) + 2 <= 1950:
            chunks[-1] = f"{last}\n{footer}"
        else:
            chunks.append(footer)
    return chunks
