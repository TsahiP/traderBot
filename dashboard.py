"""Local API for the trade bot.

Run:
    python dashboard.py
The Next.js frontend (web/) proxies /api/* to this server (port 8000).
"""
import json
import logging
import math
import os
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask.json.provider import DefaultJSONProvider

# Load .env before config; override=True so .env wins over stale shell env vars.
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

import alpaca_service
import config
import crypto_analysis
import engine
import stock_analysis
import strategies
from strategy import compute_signals, latest_signal

NY = ZoneInfo("America/New_York")

# Configure dashboard logging
logger = logging.getLogger("tradebot_api")
logger.setLevel(logging.INFO)
_dash_handler = RotatingFileHandler(
    config.LOG_DIR / "dashboard.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_dash_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
if not logger.handlers:
    logger.addHandler(_dash_handler)

def sanitize_for_json(obj):
    """Recursively replace NaN and Inf float values so JSON output is RFC 8259 compliant."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0
        return obj
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return [sanitize_for_json(v) for v in obj]
    if pd.isna(obj) and not isinstance(obj, (str, bool)):
        return None
    return obj


class SafeJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        sanitized = sanitize_for_json(obj)
        return super().dumps(sanitized, **kwargs)


app = Flask(__name__)
app.json_provider_class = SafeJSONProvider
app.json = SafeJSONProvider(app)

TRADES_FILE = config.OUTPUT_DIR / "live_trades.csv"
TRADES_BT_FILE = config.OUTPUT_DIR / "trades.csv"
EQUITY_FILE = config.OUTPUT_DIR / "equity_curve.csv"

MAX_SERIES_BARS = 800

# yfinance caps how far back intraday history goes - requested windows are
# clamped per interval (1m ~7 days, 5m-30m ~60 days, 1h ~730 days).
TF_WINDOW_DAYS = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "1h": 730}


def parse_date(value: str | None, label: str) -> date | None:
    """Parse an optional YYYY-MM-DD query param. Raises ValueError."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid {label} - use YYYY-MM-DD")


def fmt_ts(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)[:16].replace("T", " ")


def load_live_trades() -> list[dict]:
    if not TRADES_FILE.exists():
        return []
    try:
        rows = pd.read_csv(TRADES_FILE, dtype={"qty": float})
        trades = []
        for _, r in rows.iterrows():
            entry_p = float(r["entry_price"]) if pd.notna(r.get("entry_price")) else 0.0
            exit_p = float(r["exit_price"]) if pd.notna(r.get("exit_price")) else 0.0
            pnl_val = float(r["pnl"]) if pd.notna(r.get("pnl")) else 0.0
            trades.append({
                "symbol": str(r["symbol"]) if pd.notna(r.get("symbol")) else config.SYMBOL,
                "entry_date": fmt_ts(r.get("entry_date")),
                "entry_price": entry_p if not (np.isnan(entry_p) or np.isinf(entry_p)) else 0.0,
                "exit_date": fmt_ts(r.get("exit_date")),
                "exit_price": exit_p if not (np.isnan(exit_p) or np.isinf(exit_p)) else 0.0,
                "qty": int(r["qty"]) if pd.notna(r.get("qty")) else 0,
                "pnl": pnl_val if not (np.isnan(pnl_val) or np.isinf(pnl_val)) else 0.0,
                "source": "live",
            })
        return trades
    except Exception as exc:
        logger.warning("Error reading live trades: %s", exc)
        return []


def load_backtest_trades() -> list[dict]:
    if not TRADES_BT_FILE.exists():
        return []
    try:
        rows = pd.read_csv(TRADES_BT_FILE)
        trades = []
        for _, r in rows.iterrows():
            entry_p = float(r["entry_price"]) if pd.notna(r.get("entry_price")) else 0.0
            exit_p = float(r["exit_price"]) if pd.notna(r.get("exit_price")) else None
            pnl_val = float(r["pnl"]) if pd.notna(r.get("pnl")) else 0.0
            costs_val = float(r.get("costs", 0.0)) if pd.notna(r.get("costs")) else 0.0
            trades.append({
                "symbol": str(r["symbol"]) if pd.notna(r.get("symbol")) else config.SYMBOL,
                "entry_date": fmt_ts(r.get("entry_date")),
                "entry_price": entry_p if not (np.isnan(entry_p) or np.isinf(entry_p)) else 0.0,
                "exit_date": fmt_ts(r["exit_date"]) if pd.notna(r.get("exit_date")) else None,
                "exit_price": (
                    exit_p if exit_p is not None and not (np.isnan(exit_p) or np.isinf(exit_p)) else None
                ),
                "qty": int(r.get("qty", config.QUANTITY)),
                "pnl": pnl_val if not (np.isnan(pnl_val) or np.isinf(pnl_val)) else 0.0,
                "costs": costs_val if not (np.isnan(costs_val) or np.isinf(costs_val)) else 0.0,
                "source": "backtest",
            })
        return trades
    except Exception as exc:
        logger.warning("Error reading backtest trades: %s", exc)
        return []


def load_backtest_curve() -> pd.DataFrame | None:
    if not EQUITY_FILE.exists():
        return None
    try:
        df = pd.read_csv(EQUITY_FILE, index_col="date")
        df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
        df = df.dropna(subset=["equity", "Close"])
        return df if not df.empty else None
    except Exception as exc:
        logger.warning("Error reading equity curve: %s", exc)
        return None


def live_snapshot() -> dict:
    if not alpaca_service.has_alpaca_credentials():
        return {"connected": False, "reason": "missing .env keys - copy .env.example and add paper keys"}

    try:
        trading = alpaca_service.get_trading_client(paper=config.ALPACA_PAPER)
        data = alpaca_service.get_data_client()

        clock = trading.get_clock()
        bars = alpaca_service.get_last_completed_bars(data, symbol=config.SYMBOL, limit=config.ALPACA_BARS_LIMIT)
        if bars.empty:
            return {"connected": False, "reason": "No market bars returned from Alpaca"}

        df = compute_signals(bars, config.SMA_FAST, config.SMA_SLOW)
        signal = latest_signal(df)
        snapshot = alpaca_service.get_account_snapshot(trading, symbol=config.SYMBOL)

        return {
            "connected": True,
            "market_open": bool(clock.is_open),
            "symbol": config.SYMBOL,
            "close": float(df["Close"].iloc[-1]),
            "sma_fast": float(df["sma_fast"].iloc[-1]),
            "sma_slow": float(df["sma_slow"].iloc[-1]),
            "signal": signal,
            "account": snapshot["account"],
            "position": snapshot["position"],
        }
    except Exception as exc:
        logger.warning("Alpaca snapshot error: %s", exc)
        return {"connected": False, "reason": f"Alpaca error: {exc}"}


def build_run_payload(curve: pd.DataFrame, trades_df: pd.DataFrame, capital: float) -> dict:
    """Downsample the curve for the browser and extract buy/sell markers."""
    if curve is None or curve.empty:
        return {
            "series": {
                "dates": [],
                "open": [],
                "high": [],
                "low": [],
                "close": [],
                "volume": [],
                "sma_fast": [],
                "sma_slow": [],
                "equity": [],
            },
            "markers": [],
            "metrics": engine.compute_metrics(curve, trades_df, capital),
            "trades": [],
        }

    n = len(curve)
    step = max(1, (n + MAX_SERIES_BARS - 1) // MAX_SERIES_BARS)
    sub = curve.iloc[::step]
    pos_of = lambda orig_pos: min(orig_pos // step, len(sub) - 1)

    intraday = str(curve.index[0])[11:16] != "00:00"
    ts = fmt_ts if intraday else (lambda v: str(v.date()) if hasattr(v, "date") else str(v)[:10])

    def _safe_float(v, default=0.0):
        try:
            if v is None or pd.isna(v):
                return default
            f = float(v)
            if np.isnan(f) or np.isinf(f):
                return default
            return round(f, 2)
        except (ValueError, TypeError):
            return default

    def _safe_int(v, default=0):
        try:
            if v is None or pd.isna(v):
                return default
            f = float(v)
            if np.isnan(f) or np.isinf(f):
                return default
            return int(f)
        except (ValueError, TypeError):
            return default

    markers = []
    order_idx = curve.index[curve["exec"] != ""].tolist()
    for orig in order_idx:
        row = curve.loc[orig]
        markers.append({
            "index": pos_of(curve.index.get_loc(orig)),
            "date": ts(orig),
            "side": str(row["exec"]),
            "price": _safe_float(row.get("Open", 0.0)),
        })
    eod_idx = curve.index[curve["eod_exit"] != ""].tolist()
    for orig in eod_idx:
        markers.append({
            "index": pos_of(curve.index.get_loc(orig)),
            "date": ts(orig),
            "side": str(curve.at[orig, "eod_exit"]),
            "eod": True,
            "price": _safe_float(curve.at[orig, "Close"]),
        })

    has_sma = "sma_fast" in curve.columns
    return {
        "series": {
            "dates": [ts(d) for d in sub.index],
            "open": [_safe_float(v) for v in sub["Open"]],
            "high": [_safe_float(v) for v in sub["High"]],
            "low": [_safe_float(v) for v in sub["Low"]],
            "close": [_safe_float(v) for v in sub["Close"]],
            "volume": [_safe_int(v) for v in sub["Volume"]],
            "sma_fast": (
                [_safe_float(v, default=None) if pd.notna(v) and not np.isinf(float(v)) else None for v in sub["sma_fast"]]
                if has_sma else [None] * len(sub)
            ),
            "sma_slow": (
                [_safe_float(v, default=None) if pd.notna(v) and not np.isinf(float(v)) else None for v in sub["sma_slow"]]
                if has_sma else [None] * len(sub)
            ),
            "equity": [_safe_float(v, default=capital) for v in sub["equity"]],
        },
        "markers": markers,
        "metrics": engine.compute_metrics(curve, trades_df, capital),
        "trades": [
            {
                "entry_date": ts(r["entry_date"]),
                "entry_price": _safe_float(r["entry_price"]),
                "exit_date": (
                    None
                    if r["exit_date"] is None or pd.isna(r["exit_date"])
                    else ts(r["exit_date"])
                ),
                "exit_price": (
                    _safe_float(r["exit_price"], default=None)
                    if r["exit_price"] is not None and not pd.isna(r["exit_price"])
                    else None
                ),
                "side": r["side"],
                "exit_type": r["exit_type"],
                "pnl": _safe_float(r["pnl"]),
                "costs": _safe_float(r["costs"]),
            }
            for _, r in trades_df.iterrows()
        ],
    }


@app.route("/api/stats")
def api_stats():
    live = load_live_trades()
    curve = load_backtest_curve()

    realized = {
        "trades": len(live),
        "total_pnl": round(sum(t.get("pnl", 0.0) for t in live if not np.isnan(t.get("pnl", 0.0))), 2),
        "wins": sum(1 for t in live if t.get("pnl", 0.0) > 0),
    }
    if realized["trades"]:
        realized["win_rate"] = realized["wins"] / realized["trades"]

    backtest = None
    if curve is not None and not curve.empty:
        bt = load_backtest_trades()
        backtest = engine.compute_metrics(curve, pd.DataFrame(bt), config.CAPITAL)

    return jsonify({"realized": realized, "backtest": backtest})


@app.route("/api/trades")
def api_trades():
    trades = load_live_trades() + load_backtest_trades()
    trades.sort(key=lambda t: t.get("exit_date") or t.get("entry_date") or "", reverse=True)
    return jsonify({"trades": trades})


@app.route("/api/equity")
def api_equity():
    curve = load_backtest_curve()
    if curve is None or curve.empty:
        return jsonify({"dates": [], "equity": []})
    step = max(1, len(curve) // 600)
    sub = curve.iloc[::step]
    return jsonify({
        "dates": [str(d.date()) if hasattr(d, "date") else str(d)[:10] for d in sub.index],
        "equity": [float(v) if pd.notna(v) and not (np.isnan(float(v)) or np.isinf(float(v))) else 0.0 for v in sub["equity"]],
    })


@app.route("/api/live")
def api_live():
    snap = live_snapshot()

    if not snap["connected"]:
        curve = load_backtest_curve()
        if curve is not None and not curve.empty:
            last = curve.iloc[-1]
            close_v = float(last["Close"]) if pd.notna(last.get("Close")) else 0.0
            sma_f = (
                float(last["sma_fast"])
                if "sma_fast" in last and pd.notna(last["sma_fast"]) and not np.isinf(float(last["sma_fast"]))
                else None
            )
            sma_s = (
                float(last["sma_slow"])
                if "sma_slow" in last and pd.notna(last["sma_slow"]) and not np.isinf(float(last["sma_slow"]))
                else None
            )
            return jsonify({
                **snap,
                "symbol": config.SYMBOL,
                "close": close_v if not (np.isnan(close_v) or np.isinf(close_v)) else 0.0,
                "sma_fast": sma_f,
                "sma_slow": sma_s,
                "signal": int(last["signal"]) if last.get("signal") and pd.notna(last.get("signal")) else 0,
                "fallback": "backtest",
            })
    return jsonify(snap)


@app.route("/api/strategies")
def api_strategies():
    return jsonify([
        {
            "id": sid,
            "label": spec["label"],
            "description": spec["description"],
            "timeframes": spec["timeframes"],
            "flat_eod": spec["flat_eod"],
            "default_allow_short": spec["default_allow_short"],
            "default_timeframe": spec["default_timeframe"],
            "params": spec["params"],
        }
        for sid, spec in strategies.STRATEGIES.items()
    ])


@app.route("/api/watchlist")
def api_watchlist():
    return jsonify({"watchlist": config.WATCHLIST})


@app.route("/api/stock-analysis")
def api_stock_analysis():
    symbol = (request.args.get("symbol") or config.SYMBOL).strip().upper()
    if not symbol or len(symbol) > 12 or not symbol.replace(".", "").replace("-", "").isalnum():
        return jsonify({"error": "Invalid symbol - use e.g. AAPL, NVDA, SPY"}), 400
    try:
        result = stock_analysis.analyze_stock(symbol)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        logger.exception("Stock analysis error for %s: %s", symbol, exc)
        return jsonify({"error": f"Failed analyzing {symbol}: {exc}"}), 500


@app.route("/api/crypto-analysis")
def api_crypto_analysis():
    symbol = request.args.get("symbol")
    try:
        if symbol and symbol.strip():
            sym = symbol.strip().upper()
            if not sym.replace(".", "").replace("-", "").isalnum():
                return jsonify({"error": "Invalid crypto symbol - use e.g. BTC-USD, ETH-USD"}), 400
            result = crypto_analysis.analyze_crypto(sym)
            return jsonify(result)
        else:
            overview = crypto_analysis.get_crypto_top20_overview()
            return jsonify(overview)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        logger.exception("Crypto analysis error: %s", exc)
        return jsonify({"error": f"Failed analyzing crypto: {exc}"}), 500


def run_backtest_from(raw) -> tuple[dict | None, int, str | None]:
    """Parse a run request (query params or JSON body) and execute it.

    Returns (payload, status, error). payload is None when error is set.
    """
    try:
        symbol = (raw.get("symbol") or config.SYMBOL).strip().upper()
        strategy_id = (raw.get("strategy") or "sma_crossover").strip()
        timeframe = (raw.get("timeframe") or "1d").strip()
        qty = int(raw.get("qty") or config.QUANTITY)
        capital = float(raw.get("capital") or config.CAPITAL)
        allow_short_raw = raw.get("allow_short")
        if isinstance(allow_short_raw, bool):
            allow_short = allow_short_raw
        else:
            allow_short = str(allow_short_raw or "false").lower() in ("1", "true", "yes")
        cost_per_share = float(raw.get("cost_per_share") or 0.0)
    except (ValueError, TypeError):
        return None, 400, "Invalid parameter values"

    if not symbol or len(symbol) > 12 or not symbol.replace(".", "").replace("-", "").isalnum():
        return None, 400, "Invalid symbol - use e.g. SPY, AAPL, BTC-USD"
    if strategy_id not in strategies.STRATEGIES:
        return None, 400, f"Unknown strategy '{strategy_id}'"
    spec = strategies.STRATEGIES[strategy_id]
    if timeframe not in spec["timeframes"]:
        return None, 400, f"'{timeframe}' not supported by {spec['label']}"
    if qty < 1 or qty > 100_000 or capital <= 0:
        return None, 400, "qty must be 1-100000 and capital > 0"
    if cost_per_share < 0 or cost_per_share > 1:
        return None, 400, "cost_per_share must be $0-$1"
    try:
        params = strategies.parse_params(spec, raw)
    except ValueError as exc:
        return None, 400, str(exc)

    if strategy_id == "sma_crossover" and params["fast"] >= params["slow"]:
        return None, 400, "SMA periods must satisfy fast < slow"
    if strategy_id == "vwap_reversion" and params["exit_pct"] >= params["deviation_pct"]:
        return None, 400, "Exit level must be below the entry deviation"
    if strategy_id == "rsi_mean_reversion" and not (
        params["oversold"] < params["exit_level"] < params["overbought"]
    ):
        return None, 400, "Need oversold < exit level < overbought"

    import yfinance as yf

    try:
        start_d = parse_date(raw.get("start"), "start date")
        end_d = parse_date(raw.get("end"), "end date")
    except ValueError as exc:
        return None, 400, str(exc)
    if start_d and end_d and start_d > end_d:
        return None, 400, "Start date must be on or before the end date"

    kwargs: dict = {"auto_adjust": True, "actions": False}
    if timeframe == "1d":
        kwargs["start"] = start_d.isoformat() if start_d else config.BACKTEST_START
        if end_d:
            kwargs["end"] = (end_d + timedelta(days=1)).isoformat()
    else:
        kwargs["interval"] = timeframe
        now = datetime.now(NY)
        allowed_start = now - timedelta(days=TF_WINDOW_DAYS[timeframe] - 1)
        s = start_d or allowed_start.date()
        if s < allowed_start.date():
            s = allowed_start.date()
        kwargs["start"] = s.isoformat()
        if end_d:
            kwargs["end"] = (min(end_d, now.date()) + timedelta(days=1)).isoformat()

    try:
        df = yf.Ticker(symbol).history(**kwargs)
    except Exception as exc:
        logger.warning("yfinance error for %s: %s", symbol, exc)
        return None, 404, f"Failed to download data for '{symbol}'"

    if df.empty:
        return None, 404, f"No data for '{symbol}' - check the ticker symbol"

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[(df["Open"] > 0) & (df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)]
    if df.empty or len(df) < 2:
        return None, 400, f"Insufficient price history for '{symbol}' - please choose a wider date range"

    flat_eod = spec["flat_eod"] and timeframe != "1d"
    try:
        curve, trades_df = engine.run_backtest(
            df,
            strategy=strategy_id,
            params=params,
            qty=qty,
            capital=capital,
            allow_short=allow_short,
            cost_per_share=cost_per_share,
            flat_eod=flat_eod,
        )
        payload = build_run_payload(curve, trades_df, capital)
        payload["meta"] = {
            "symbol": symbol,
            "strategy": strategy_id,
            "strategy_label": spec["label"],
            "timeframe": timeframe,
            "qty": qty,
            "capital": capital,
            "allow_short": allow_short,
            "cost_per_share": cost_per_share,
            "flat_eod": flat_eod,
            "params": {k: v for k, v in params.items()},
        }
        return payload, 200, None
    except Exception as exc:
        logger.exception("Engine failure for %s (%s): %s", symbol, strategy_id, exc)
        return None, 500, f"Backtest engine error: {exc}"


@app.route("/api/backtest/run")
def api_backtest_run():
    payload, status, error = run_backtest_from(request.args)
    if error:
        return jsonify({"error": error}), status
    return jsonify(payload)


# ---- Local LLM advisor (LM Studio, OpenAI-compatible API) ----

SYSTEM_PROMPT = """You are a quantitative trading analyst. You review backtest results from a strategy lab and give honest, actionable advice to a retail trader.

Rules:
- Be brutally honest. If the strategy loses money after costs, say so plainly.
- Suggest 1-3 concrete alternative parameter sets, each value inside the allowed ranges given, with a one-line rationale per set. Only tune the strategy params from allowed_param_ranges - qty, capital and cost_per_share are execution settings, leave them alone.
- Separate the edge (win rate, avg win vs avg loss, long vs short) from cost drag (costs_total, cost_per_share) and data limits (short intraday history).
- Never claim future performance, never invent numbers, no hype.
- Answer with exactly three short markdown sections: "## Diagnosis", "## Suggested configurations", "## Risks".
- Keep it under 350 words."""

_auto_llm_model: str | None = None


def resolve_llm_model() -> str:
    """Pick the model to chat with. Priority: .env LLM_MODEL > config default; auto only if LLM_MODEL=auto."""
    global _auto_llm_model
    model, auto_pick = config.resolve_llm_model_setting()
    if not auto_pick:
        return model
    if _auto_llm_model:
        return _auto_llm_model
    import requests as _requests

    r = _requests.get(f"{config.LLM_BASE_URL}/models", timeout=10)
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", [])]
    chat_ids = [m for m in ids if "embed" not in m.lower()]
    qwen_chat = [m for m in chat_ids if "qwen" in m.lower() and "vl" not in m.lower()]
    pick = (
        next((m for m in chat_ids if "hermes" in m.lower()), None)
        or next((m for m in qwen_chat if "qwen3" in m.lower()), None)
        or (qwen_chat[0] if qwen_chat else None)
        or (chat_ids[0] if chat_ids else None)
    )
    if not pick:
        raise RuntimeError("no chat models found in LM Studio")
    _auto_llm_model = pick
    return pick


def llm_chat(messages: list[dict]) -> str:
    """One chat round trip against the local LM Studio server."""
    import requests as _requests

    model = resolve_llm_model()
    r = _requests.post(
        f"{config.LLM_BASE_URL}/chat/completions",
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 800,
        },
        timeout=600,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def build_analysis_context(payload: dict, spec: dict) -> str:
    """Compact, model-friendly digest of a run + its allowed param ranges."""
    meta, metrics, trades = payload["meta"], payload["metrics"], payload["trades"]
    closed = [t for t in trades if t["exit_type"] != "open"]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    long_trades = [t for t in closed if t["side"] == "long"]
    short_trades = [t for t in closed if t["side"] == "short"]

    return json.dumps(
        sanitize_for_json({
            "config": {
                "symbol": meta["symbol"],
                "strategy": meta["strategy_label"],
                "timeframe": meta["timeframe"],
                "params": meta["params"],
                "allow_short": meta["allow_short"],
                "qty": meta["qty"],
                "capital": meta["capital"],
                "cost_per_share": meta["cost_per_share"],
                "flat_eod": meta["flat_eod"],
            },
            "results_decimal_fractions": {
                "start": metrics["start"],
                "end": metrics["end"],
                "total_return": metrics["total_return"],
                "cagr": metrics["cagr"],
                "buy_hold": metrics["buy_hold"],
                "max_drawdown": metrics["max_drawdown"],
                "final_equity": metrics["final_equity"],
                "trades": metrics["trades"],
                "wins": metrics["wins"],
                "win_rate": metrics["win_rate"],
                "costs_total": metrics["costs_total"],
            },
            "trade_stats": {
                "avg_win": avg([t["pnl"] for t in wins]),
                "avg_loss": avg([t["pnl"] for t in losses]),
                "long_trades": len(long_trades),
                "short_trades": len(short_trades),
                "long_pnl": round(sum(t["pnl"] for t in long_trades), 2),
                "short_pnl": round(sum(t["pnl"] for t in short_trades), 2),
                "eod_exits": sum(1 for t in closed if t["exit_type"] == "eod"),
                "open_positions": len(trades) - len(closed),
            },
            "allowed_param_ranges": [
                {
                    "key": p["key"],
                    "label": p["label"],
                    "min": p["min"],
                    "max": p["max"],
                    "step": p["step"],
                    "unit": p.get("unit", ""),
                }
                for p in spec["params"]
            ],
            "strategy_notes": spec["description"],
        }),
        indent=1,
        default=str,
    )


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Re-run the submitted backtest and have the local LLM critique it."""
    body = request.get_json(silent=True) or {}
    payload, status, error = run_backtest_from(body)
    if error:
        return jsonify({"error": error}), status

    spec = strategies.STRATEGIES[payload["meta"]["strategy"]]
    context = build_analysis_context(payload, spec)
    try:
        model = resolve_llm_model()
        analysis = llm_chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Analyze this backtest run and suggest better parameter "
                        f"configurations.\n\n{context}"
                    ),
                },
            ]
        )
    except Exception as exc:
        logger.warning("LLM analyze failure: %s", exc)
        return (
            jsonify(
                {
                    "error": (
                        f"Local LLM unavailable ({exc}). Is LM Studio running "
                        f"at {config.LLM_BASE_URL} with a model loaded?"
                    )
                }
            ),
            503,
        )
    return jsonify({"model": model, "analysis": analysis})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
