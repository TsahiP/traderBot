"""Local API for the trade bot.

Run:
    python dashboard.py
The Next.js frontend (web/) proxies /api/* to this server (port 8000).
"""
import json
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, request

import core.config as config
import integrations.discord_sync as discord_sync
import core.engine as engine
import integrations.funda_client as funda_client
import integrations.news_client as news_client
import signaling.signals as signals
import core.strategies as strategies
from market.bybit_client import BybitError
from charts.candle_patterns import PATTERNS as SIGNAL_PATTERNS
from livebot import get_last_completed_bars
from integrations.llm_client import llm_chat, resolve_llm_model
from market.market_data import fetch_ohlcv, fetch_ohlcv_period, fetch_yfinance_history
from market.market_symbols import is_bybit_crypto, is_valid_symbol, normalize_symbol
from core.strategy import compute_signals, latest_signal

load_dotenv(config.BASE_DIR / ".env")
NY = ZoneInfo("America/New_York")

app = Flask(__name__)

TRADES_FILE = config.OUTPUT_DIR / "live_trades.csv"
TRADES_BT_FILE = config.OUTPUT_DIR / "trades.csv"
PAPER_DB_PATH = config.PAPERBOT_DB_PATH      # paperbot's journal, opened read-only
PAPER_ONLINE_S = 10                           # heartbeat younger than this = bot online
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
    return str(value)[:16].replace("T", " ")


def load_live_trades() -> list[dict]:
    if not TRADES_FILE.exists():
        return []
    rows = pd.read_csv(TRADES_FILE, dtype={"qty": float})
    return [
        {
            "entry_date": fmt_ts(r["entry_date"]),
            "entry_price": float(r["entry_price"]),
            "exit_date": fmt_ts(r["exit_date"]),
            "exit_price": float(r["exit_price"]),
            "qty": int(r["qty"]),
            "pnl": float(r["pnl"]),
            "source": "live",
        }
        for _, r in rows.iterrows()
    ]


def load_backtest_trades() -> list[dict]:
    if not TRADES_BT_FILE.exists():
        return []
    rows = pd.read_csv(TRADES_BT_FILE)
    return [
        {
            "entry_date": fmt_ts(r["entry_date"]),
            "entry_price": float(r["entry_price"]),
            "exit_date": fmt_ts(r["exit_date"]),
            "exit_price": float(r["exit_price"]),
            "qty": config.QUANTITY,
            "pnl": float(r["pnl"]),
            "source": "backtest",
        }
        for _, r in rows.iterrows()
    ]


def _paper_journal():
    """Read-only view of paperbot.db, or None before the bot ever ran."""
    from paper.journal import Journal

    try:
        return Journal(PAPER_DB_PATH, readonly=True)
    except FileNotFoundError:
        return None


def load_paper_trades() -> list[dict]:
    j = _paper_journal()
    if j is None:
        return []
    try:
        rows = j.trades(closed=True)
    finally:
        j.close()
    return [
        {
            "entry_date": fmt_ts(r["entry_ts"]),
            "entry_price": float(r["entry_price"]),
            "exit_date": fmt_ts(r["exit_ts"]),
            "exit_price": float(r["exit_price"]),
            "qty": int(round(r["qty"])),
            "pnl": float(r["pnl"]),
            "source": "paper",
            "symbol": r["symbol"],
            "side": r["side"],
            "r": r["r"],
            "exit_reason": r["exit_reason"],
            "run_id": r["run_id"],
        }
        for r in rows
    ]


def load_backtest_curve() -> pd.DataFrame | None:
    if not EQUITY_FILE.exists():
        return None
    df = pd.read_csv(EQUITY_FILE, index_col="date")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
    return df


def live_snapshot() -> dict:
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    if not key or not secret:
        return {"connected": False, "reason": "missing .env keys - copy .env.example and add paper keys"}

    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient

        trading = TradingClient(key, secret, paper=True)
        data = StockHistoricalDataClient(key, secret)

        clock = trading.get_clock()
        bars = get_last_completed_bars(data, datetime.now(NY))
        df = compute_signals(bars, config.SMA_FAST, config.SMA_SLOW)
        signal = latest_signal(df)

        account = trading.get_account()
        position = None
        try:
            p = trading.get_open_position(config.SYMBOL)
            position = {
                "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "current": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_pl_pct": float(p.unrealized_plpc),
            }
        except Exception:
            position = None

        return {
            "connected": True,
            "market_open": bool(clock.is_open),
            "symbol": config.SYMBOL,
            "close": float(df["Close"].iloc[-1]),
            "sma_fast": float(df["sma_fast"].iloc[-1]),
            "sma_slow": float(df["sma_slow"].iloc[-1]),
            "signal": signal,
            "account": {
                "equity": float(account.equity),
                "cash": float(account.cash),
                "buying_power": float(account.buying_power),
            },
            "position": position,
        }
    except Exception as exc:
        return {"connected": False, "reason": f"Alpaca error: {exc}"}


def _opt_round(value, digits: int) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if np.isfinite(f) else None


def build_run_payload(curve: pd.DataFrame, trades_df: pd.DataFrame, capital: float) -> dict:
    """Downsample the curve for the browser and extract buy/sell markers."""
    n = len(curve)
    step = max(1, (n + MAX_SERIES_BARS - 1) // MAX_SERIES_BARS)
    sub = curve.iloc[::step]
    pos_of = lambda orig_pos: min(orig_pos // step, len(sub) - 1)

    intraday = str(curve.index[0])[11:16] != "00:00"
    ts = fmt_ts if intraday else (lambda v: str(v.date()))

    markers = []
    order_idx = curve.index[curve["exec"] != ""].tolist()
    for orig in order_idx:
        row = curve.loc[orig]
        markers.append({
            "index": pos_of(curve.index.get_loc(orig)),
            "date": ts(orig),
            "side": str(row["exec"]),
            "price": round(float(row["Open"]), 2),
        })
    eod_idx = curve.index[curve["eod_exit"] != ""].tolist()
    for orig in eod_idx:
        markers.append({
            "index": pos_of(curve.index.get_loc(orig)),
            "date": ts(orig),
            "side": str(curve.at[orig, "eod_exit"]),
            "eod": True,
            "price": round(float(curve.at[orig, "Close"]), 2),
        })
    if "bracket_exit" in curve.columns:
        for orig in curve.index[curve["bracket_exit"] != ""].tolist():
            markers.append({
                "index": pos_of(curve.index.get_loc(orig)),
                "date": ts(orig),
                "side": str(curve.at[orig, "bracket_exit"]),
                "exit": str(curve.at[orig, "bracket_kind"]),
                "price": round(float(curve.at[orig, "bracket_price"]), 2),
            })

    has_sma = "sma_fast" in curve.columns
    return {
        "series": {
            "dates": [ts(d) for d in sub.index],
            "open": [round(float(v), 2) for v in sub["Open"]],
            "high": [round(float(v), 2) for v in sub["High"]],
            "low": [round(float(v), 2) for v in sub["Low"]],
            "close": [round(float(v), 2) for v in sub["Close"]],
            "volume": [int(v) for v in sub["Volume"]],
            "sma_fast": (
                [round(float(v), 2) if not np.isnan(v) else None for v in sub["sma_fast"]]
                if has_sma else [None] * len(sub)
            ),
            "sma_slow": (
                [round(float(v), 2) if not np.isnan(v) else None for v in sub["sma_slow"]]
                if has_sma else [None] * len(sub)
            ),
            "equity": [round(float(v), 2) for v in sub["equity"]],
        },
        "markers": markers,
        "metrics": engine.compute_metrics(curve, trades_df, capital),
        "trades": [
            {
                "entry_date": ts(r["entry_date"]),
                "entry_price": round(float(r["entry_price"]), 2),
                "exit_date": (
                    None
                    if r["exit_date"] is None or pd.isna(r["exit_date"])
                    else ts(r["exit_date"])
                ),
                "exit_price": (
                    round(float(r["exit_price"]), 2)
                    if r["exit_price"] is not None and not pd.isna(r["exit_price"])
                    else None
                ),
                "side": r["side"],
                "exit_type": r["exit_type"],
                "pnl": round(float(r["pnl"]), 2),
                "costs": round(float(r["costs"]), 2),
                "r_multiple": _opt_round(r.get("r_multiple"), 2),
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
        "total_pnl": round(sum(t["pnl"] for t in live), 2),
        "wins": sum(1 for t in live if t["pnl"] > 0),
    }
    if realized["trades"]:
        realized["win_rate"] = realized["wins"] / realized["trades"]

    backtest = None
    if curve is not None:
        bt = load_backtest_trades()
        backtest = engine.compute_metrics(curve, pd.DataFrame(bt), config.CAPITAL)

    return jsonify({"realized": realized, "backtest": backtest})


@app.route("/api/trades")
def api_trades():
    trades = load_live_trades() + load_paper_trades() + load_backtest_trades()
    trades.sort(key=lambda t: t["exit_date"], reverse=True)
    return jsonify({"trades": trades})


@app.route("/api/equity")
def api_equity():
    curve = load_backtest_curve()
    if curve is None:
        return jsonify({"dates": [], "equity": []})
    step = max(1, len(curve) // 600)
    sub = curve.iloc[::step]
    return jsonify({
        "dates": [str(d.date()) for d in sub.index],
        "equity": [float(v) for v in sub["equity"]],
    })


@app.route("/api/live")
def api_live():
    snap = live_snapshot()

    if not snap["connected"]:
        curve = load_backtest_curve()
        if curve is not None:
            last = curve.iloc[-1]
            return jsonify({
                **snap,
                "symbol": config.SYMBOL,
                "close": float(last["Close"]),
                "sma_fast": float(last["sma_fast"]),
                "sma_slow": float(last["sma_slow"]),
                "signal": int(last["signal"]) if last["signal"] else 0,
                "fallback": "backtest",
            })
    return jsonify(snap)


# ---- Paper desk (read-only; the live bot serves its own /state and /stream on :8100) ----

def _paper_read(fn, empty):
    j = _paper_journal()
    if j is None:
        return jsonify(empty)
    try:
        return jsonify(fn(j))
    finally:
        j.close()


def _paper_online(heartbeat: dict | None) -> bool:
    if not heartbeat or not heartbeat.get("ts"):
        return False
    try:
        age = (datetime.now(NY) - datetime.fromisoformat(heartbeat["ts"])).total_seconds()
    except ValueError:
        return False
    return age < PAPER_ONLINE_S


@app.route("/api/paper/state")
def api_paper_state():
    def build(j):
        day = datetime.now(NY).date().isoformat()
        hb = j.read_heartbeat()
        entries = j.entries_today(day)
        return {
            "online": _paper_online(hb), "empty": False, "heartbeat": hb,
            "open_trades": j.open_trades(),
            "proposals": j.decisions_by_outcome("proposed"),
            "setups": j.setups(day),
            "counters": {"entries_today": len(entries),
                         "daily_loss_used": round(sum(e["max_loss"] or 0.0 for e in entries), 2)},
            "last_event_id": j.last_event_id(),
        }

    return _paper_read(build, {"online": False, "empty": True})


@app.route("/api/paper/trades")
def api_paper_trades():
    run_id = request.args.get("run_id") or None
    return _paper_read(lambda j: {"trades": j.trades(run_id=run_id, closed=True)}, {"trades": []})


@app.route("/api/paper/decisions")
def api_paper_decisions():
    day = request.args.get("day") or None
    return _paper_read(lambda j: {"decisions": j.decisions(day=day)}, {"decisions": []})


@app.route("/api/paper/events")
def api_paper_events():
    try:
        after = int(request.args.get("after", 0))
        limit = min(int(request.args.get("limit", 500)), 2000)
    except ValueError:
        return jsonify({"error": "after and limit must be numbers"}), 400
    return _paper_read(lambda j: {"events": j.events_since(after, limit=limit)}, {"events": []})


@app.route("/api/paper/equity")
def api_paper_equity():
    since = request.args.get("since") or None
    return _paper_read(lambda j: {"equity": j.equity(since=since)}, {"equity": []})


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


def fetch_history(symbol: str, kwargs: dict) -> pd.DataFrame:
    """Fetch OHLCV history with a short retry on the stock (yfinance) path.

    Tickers ending in `-USD` go to Bybit spot. Yahoo/yfinance occasionally
    returns an empty frame on a transient blip (rate limit, cold cookie/crumb).
    Retry a couple of times before giving up so a valid stock ticker doesn't
    surface as a false 404.
    """
    symbol = normalize_symbol(symbol)
    if is_bybit_crypto(symbol):
        return fetch_ohlcv(
            symbol,
            timeframe=kwargs.get("interval") or "1d",
            start=kwargs.get("start"),
            end=kwargs.get("end"),
        )
    return fetch_yfinance_history(symbol, **kwargs)


def run_backtest_from(raw) -> tuple[dict | None, int, str | None]:
    """Parse a run request (query params or JSON body) and execute it.

    Returns (payload, status, error). payload is None when error is set.
    """
    try:
        symbol = normalize_symbol(raw.get("symbol") or config.SYMBOL)
        strategy_id = (raw.get("strategy") or "sma_crossover").strip()
        timeframe = (raw.get("timeframe") or "1d").strip()
        qty = int(raw.get("qty") or config.QUANTITY)
        capital = float(raw.get("capital") or config.CAPITAL)
        allow_short_raw = raw.get("allow_short")
        if isinstance(allow_short_raw, bool):
            allow_short = allow_short_raw
        else:
            allow_short = (allow_short_raw or "false").lower() in ("1", "true", "yes")
        cost_per_share = float(raw.get("cost_per_share") or 0.0)
        stop_atr = float(raw.get("stop_atr") or 0.0)
        target_r = float(raw.get("target_r") or 0.0)
    except ValueError:
        return None, 400, "Invalid parameter values"

    if not is_valid_symbol(symbol):
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
    if not (0 <= stop_atr <= 10 and 0 <= target_r <= 10):
        return None, 400, "stop_atr and target_r must be 0-10 (0 = off)"
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

    try:
        start_d = parse_date(raw.get("start"), "start date")
        end_d = parse_date(raw.get("end"), "end date")
    except ValueError as exc:
        return None, 400, str(exc)
    if start_d and end_d and start_d > end_d:
        return None, 400, "Start date must be on or before the end date"

    kwargs: dict = {"auto_adjust": True, "actions": False}
    if timeframe == "1d":
        kwargs["start"] = (start_d or date.fromisoformat(config.BACKTEST_START)).isoformat()
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
        df = fetch_history(symbol, kwargs)
    except BybitError as exc:
        return None, 400, str(exc)
    if df.empty:
        return None, 404, (
            f"No data for '{symbol}' - check the ticker symbol and date range "
            f"(intraday history is limited: 1m~7d, 5m-30m~60d, 1h~730d)"
        )

    flat_eod = spec["flat_eod"] and timeframe != "1d"
    curve, trades_df = engine.run_backtest(
        df,
        strategy=strategy_id,
        params=params,
        qty=qty,
        capital=capital,
        allow_short=allow_short,
        cost_per_share=cost_per_share,
        flat_eod=flat_eod,
        stop_atr_mult=stop_atr,
        target_r=target_r,
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
        "stop_atr": stop_atr,
        "target_r": target_r,
        "params": {k: v for k, v in params.items()},
    }
    return payload, 200, None


@app.route("/api/backtest/run")
def api_backtest_run():
    payload, status, error = run_backtest_from(request.args)
    if error:
        return jsonify({"error": error}), status
    return jsonify(payload)


@app.route("/api/timemachine")
def api_timemachine():
    """Monte Carlo replay of the rest of a session from 60d of 5m bars (stocks)."""
    import tools.timemachine as timemachine

    try:
        symbol = normalize_symbol(request.args.get("symbol") or "")
        entry = float(request.args["entry"])
        stop = float(request.args["stop"])
        target = float(request.args["target"])
        side = (request.args.get("side") or "long").lower()
        runs = min(max(int(request.args.get("runs", timemachine.DEFAULT_RUNS)), 100), 20_000)
        seed = int(request.args.get("seed", timemachine.DEFAULT_SEED))
        at_raw = request.args.get("at")
        at = (datetime.strptime(at_raw, "%Y-%m-%d %H:%M").replace(tzinfo=NY)
              if at_raw else datetime.now(NY))
    except (KeyError, ValueError):
        return jsonify({"error": "need symbol, entry, stop, target (numbers); optional side, runs, seed, at='YYYY-MM-DD HH:MM' NY"}), 400
    if not is_valid_symbol(symbol) or is_bybit_crypto(symbol):
        return jsonify({"error": "stock tickers only (the replay uses NYSE sessions)"}), 400
    try:
        df5 = fetch_ohlcv_period(symbol, "5m", "60d")
        return jsonify(timemachine.replay(df5, entry=entry, stop=stop, target=target, at=at,
                                          side=side, runs=runs, seed=seed))
    except timemachine.TimeMachineError as exc:
        return jsonify({"error": str(exc)}), 400


# ---- Local LLM advisor (LM Studio, OpenAI-compatible API) ----

SYSTEM_PROMPT = """You are a quantitative trading analyst. You review backtest results from a strategy lab and give honest, actionable advice to a retail trader.

Rules:
- Be brutally honest. If the strategy loses money after costs, say so plainly.
- Suggest 1-3 concrete alternative parameter sets, each value inside the allowed ranges given, with a one-line rationale per set. Only tune the strategy params from allowed_param_ranges - qty, capital and cost_per_share are execution settings, leave them alone.
- Separate the edge (win rate, avg win vs avg loss, long vs short) from cost drag (costs_total, cost_per_share) and data limits (short intraday history).
- Never claim future performance, never invent numbers, no hype.
- Answer with exactly three short markdown sections: "## Diagnosis", "## Suggested configurations", "## Risks".
- Keep it under 350 words."""


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
        {
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
                "stop_atr_mult": meta.get("stop_atr", 0.0),
                "target_r": meta.get("target_r", 0.0),
            },
            "results_decimal_fractions": {
                "start": metrics["start"],
                "end": metrics["end"],
                "total_return": metrics["total_return"],   # e.g. -0.27 = -27%
                "cagr": metrics["cagr"],
                "buy_hold": metrics["buy_hold"],
                "max_drawdown": metrics["max_drawdown"],
                "final_equity": metrics["final_equity"],
                "trades": metrics["trades"],
                "wins": metrics["wins"],
                "win_rate": metrics["win_rate"],
                "costs_total": metrics["costs_total"],
                "sharpe": metrics.get("sharpe"),
                "profit_factor": metrics.get("profit_factor"),
                "expectancy_usd": metrics.get("expectancy"),
                "avg_r": metrics.get("avg_r"),
            },
            "trade_stats": {
                "avg_win": avg([t["pnl"] for t in wins]),
                "avg_loss": avg([t["pnl"] for t in losses]),
                "long_trades": len(long_trades),
                "short_trades": len(short_trades),
                "long_pnl": round(sum(t["pnl"] for t in long_trades), 2),
                "short_pnl": round(sum(t["pnl"] for t in short_trades), 2),
                "eod_exits": sum(1 for t in closed if t["exit_type"] == "eod"),
                "stop_exits": sum(1 for t in closed if t["exit_type"] == "stop"),
                "target_exits": sum(1 for t in closed if t["exit_type"] == "target"),
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
        },
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


# ---- Signal bot (candlestick patterns -> Telegram) ----

@app.route("/api/signals/config")
def api_signals_config():
    token, _ = signals.telegram_credentials()
    return jsonify({
        "config": signals.load_config(),
        "patterns": [
            {"id": pid, **meta} for pid, meta in SIGNAL_PATTERNS.items()
        ],
        "telegram_configured": bool(token and signals.telegram_chat_ids()),
        "discord_configured": signals.discord_configured(),
        "discord_available_timeframes": signals.discord_timeframes_available(),
    })


def _json_object_body():
    """Parse JSON body; reject non-objects with 400."""
    body = request.get_json(silent=True)
    if body is None:
        return {}, None
    if not isinstance(body, dict):
        return None, (jsonify({"error": "JSON body must be an object"}), 400)
    return body, None


@app.route("/api/signals/config", methods=["POST"])
def api_signals_config_save():
    body, err = _json_object_body()
    if err:
        return err
    try:
        cfg = signals.save_config(body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(cfg)


@app.route("/api/signals/status")
def api_signals_status():
    hb = signals.read_heartbeat()
    running, last_check, age_s = False, None, None
    if hb:
        try:
            ts = datetime.fromisoformat(hb["ts"])
            age_s = max(0.0, (datetime.now(ts.tzinfo) - ts).total_seconds())
            last_check = hb["ts"]
            running = age_s < signals.SIGNAL_HEARTBEAT_STALE_SECONDS
        except (ValueError, TypeError):
            pass
    return jsonify({
        "running": running,
        "last_check": last_check,
        "heartbeat_age_s": age_s,
    })


@app.route("/api/signals/history")
def api_signals_history():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
    except ValueError:
        limit = 50
    include_filtered = request.args.get("include_filtered", "").lower() in ("1", "true", "yes")
    return jsonify({"signals": signals.read_signals(limit, include_filtered=include_filtered)})


@app.route("/api/signals/stats")
def api_signals_stats():
    return jsonify(signals.signal_stats())


@app.route("/api/signals/test", methods=["POST"])
def api_signals_test():
    token, _ = signals.telegram_credentials()
    chat_ids = signals.telegram_chat_ids()
    if not (token and chat_ids):
        return jsonify({"error": "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env"}), 400
    ok = signals.tg_send(token, chat_ids, text="tradebot signal test - Telegram is wired up")
    if not ok:
        return jsonify({"error": "Telegram rejected the message - check token/chat id and network"}), 502
    return jsonify({"ok": True})


@app.route("/api/signals/discord-test", methods=["POST"])
def api_signals_discord_test():
    body = request.get_json(silent=True) or {}
    route = str(body.get("route", "")).strip().lower()
    canon = signals.normalize_discord_route(route)
    if not canon:
        routes = ", ".join(signals.DISCORD_CANONICAL_ROUTES)
        legacy = ", ".join(sorted(signals.DISCORD_ROUTE_ALIASES))
        return jsonify({
            "error": f"route must be a canonical route ({routes}) or alias ({legacy})",
        }), 400
    url = signals.discord_webhook_url(canon)
    if not url:
        env_keys = signals.DISCORD_ROUTE_ENV_FALLBACKS.get(canon, ())
        hint = env_keys[0] if env_keys else canon
        return jsonify({
            "error": (
                f"{hint} missing in .env and no synced webhook for {canon} "
                "(set env or run Sync from Discord)"
            ),
        }), 400
    ok = signals.dc_send(url, text=f"tradebot signal test - Discord {canon} is wired up")
    if not ok:
        return jsonify({"error": "Discord rejected the message - check webhook URL and network"}), 502
    return jsonify({"ok": True})


@app.route("/api/signals/discord-sync", methods=["GET"])
def api_signals_discord_sync_status():
    snap = discord_sync.read_sync_snapshot()
    if not snap:
        return jsonify({"synced_at": None, "routes": {}, "unmatched_channels": [], "conflicts": []})
    return jsonify({
        "synced_at": snap.get("synced_at"),
        "routes": {
            route: {
                "channel_name": meta.get("channel_name"),
                "channel_id": meta.get("channel_id"),
            }
            for route, meta in (snap.get("routes") or {}).items()
            if isinstance(meta, dict)
        },
        "unmatched_channels": snap.get("unmatched_channels") or [],
        "conflicts": snap.get("conflicts") or [],
        "skipped_no_webhook": snap.get("skipped_no_webhook") or [],
    })


@app.route("/api/signals/discord-sync", methods=["POST"])
def api_signals_discord_sync_run():
    body, err = _json_object_body()
    if err:
        return err
    create = bool(body.get("create_webhooks", False))
    try:
        result = discord_sync.sync_discord_webhooks(create_missing_webhooks=create)
    except discord_sync.DiscordSyncError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Discord sync failed: {exc}"}), 502
    return jsonify({
        "synced_at": result.get("synced_at"),
        "routes": list((result.get("routes") or {}).keys()),
        "unmatched_channels": result.get("unmatched_channels") or [],
        "conflicts": result.get("conflicts") or [],
        "skipped_no_webhook": result.get("skipped_no_webhook") or [],
    })


# ---- Fundamentals & news (fundabot / on-demand) ----

FUNDI_HEARTBEAT_STALE_SECONDS = 15 * 60
FUNDI_REFRESH_MAX = 10         # max symbols per on-demand refresh call


@app.route("/api/fundi")
def api_fundi():
    """Watchlists grouped snapshots + bot status for the Fundi tab."""
    cfg = signals.load_config()
    lists = []
    groups: dict[str, list[str]] = {}
    for watchlist in cfg.get("lists", []):
        stocks, cryptos = [], []
        for raw in watchlist.get("symbols", []):
            symbol = normalize_symbol(raw)
            if not symbol:
                continue
            if is_bybit_crypto(symbol):
                if symbol not in cryptos:
                    cryptos.append(symbol)
            elif symbol not in stocks:
                stocks.append(symbol)
        for symbol in stocks + cryptos:
            groups.setdefault(symbol, [])
        lists.append({
            "id": watchlist["id"],
            "name": watchlist["name"],
            "stocks": stocks,
            "crypto": cryptos,
        })
    snapshots = {}
    for symbol in groups:
        snap = funda_client.read_snapshot(symbol)
        if snap is not None:  # symbols not yet refreshed are simply absent
            snapshots[symbol] = snap
    hb = None
    if config.FUNDA_HEARTBEAT_PATH.exists():
        try:
            hb = json.loads(config.FUNDA_HEARTBEAT_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            hb = None
    running, hb_ts, hb_age = False, None, None
    if hb:
        try:
            ts = datetime.fromisoformat(hb["ts"])
            hb_age = max(0.0, (datetime.now(ts.tzinfo) - ts).total_seconds())
            hb_ts, running = hb["ts"], hb_age < FUNDI_HEARTBEAT_STALE_SECONDS
        except (ValueError, TypeError, KeyError):
            pass
    digest_date = None
    try:
        digest_date = json.loads(
            (config.FUNDA_DIR / "funda_digest.json").read_text(encoding="utf-8")
        ).get("last_date")
    except (json.JSONDecodeError, OSError):
        digest_date = None
    return jsonify({
        "lists": lists,
        "snapshots": snapshots,
        "bot": {
            "running": running,
            "ts": hb_ts,
            "heartbeat_age_s": hb_age,
            "last_digest_date": digest_date,
            "news_configured": {
                "stocks": bool(signals.discord_webhook_url("stock_news")),
                "crypto": bool(signals.discord_webhook_url("crypto_news")),
            },
        },
        "quota": funda_client.quota_status(),
        "keys": funda_client.configured_keys(),
    })


@app.route("/api/fundi/news")
def api_fundi_news():
    try:
        limit = min(max(int(request.args.get("limit", 40)), 1), 200)
    except ValueError:
        limit = 40
    symbol = request.args.get("symbol")
    if symbol:
        symbol = normalize_symbol(symbol)
    from_signal_log = news_client.read_all_news(symbol=symbol, limit=limit)
    return jsonify({"news": from_signal_log, "sent_count": len(news_client.sent_ids())})


@app.route("/api/fundi/refresh", methods=["POST"])
def api_fundi_refresh():
    """On-demand snapshot refresh for one symbol (or 'all')."""
    body, err = _json_object_body()
    if err:
        return err
    symbol = str(body.get("symbol", "")).strip().upper()
    force = bool(body.get("force", True))
    if symbol in ("ALL", "ALL STOCKS", "ALL_STOCKS"):
        cfg = signals.load_config()
        stocks, _ = fundabot_watch_items(cfg)
        stocks = stocks[:FUNDI_REFRESH_MAX]
    else:
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400
        symbol = normalize_symbol(symbol)
        stocks = [symbol]
    out = {}
    for sym in stocks:
        try:
            if is_bybit_crypto(sym):
                snap = funda_client.refresh_crypto(sym, force=force)
            else:
                snap = funda_client.refresh_symbol(sym, force=force)
            out[sym] = snap
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"{sym}: {str(exc)[:140]}"}), 502
    return jsonify({
        "refreshed": list(out.keys()),
        "snapshots": out,
        "quota": funda_client.quota_status(),
    })


def fundabot_watch_items(cfg: dict) -> tuple[list[str], list[str]]:
    """Shared with fundabot: stock + crypto symbol union across watchlists."""
    stocks, cryptos = [], []
    for watchlist in cfg.get("lists", []):
        for raw in watchlist.get("symbols", []):
            symbol = normalize_symbol(raw)
            if is_bybit_crypto(symbol):
                if symbol not in cryptos:
                    cryptos.append(symbol)
            elif symbol and symbol not in stocks:
                stocks.append(symbol)
    return stocks, cryptos


# ---- Market scanner (scanner.py) ----

_scan_state: dict = {"running": False, "error": None, "started": None}
_scan_lock = threading.Lock()


def _scan_worker(mode: str, scope: str) -> None:
    import tools.scanner as scanner

    try:
        scanner.run_scan(mode, scope=scope)
        _scan_state["error"] = None
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        _scan_state["error"] = str(exc)[:200]
    finally:
        _scan_state["running"] = False


@app.route("/api/scanner")
def api_scanner():
    import tools.scanner as scanner

    return jsonify({
        "results": scanner.read_results(),
        "running": _scan_state["running"],
        "started": _scan_state["started"],
        "error": _scan_state["error"],
    })


@app.route("/api/scanner/run", methods=["POST"])
def api_scanner_run():
    body, err = _json_object_body()
    if err:
        return err
    mode = str(body.get("mode", "daily"))
    scope = str(body.get("scope", "watchlists"))
    if mode not in ("daily", "orb") or scope not in ("watchlists", "universe"):
        return jsonify({"error": "mode must be daily|orb and scope watchlists|universe"}), 400
    with _scan_lock:
        if _scan_state["running"]:
            return jsonify({"error": "a scan is already running"}), 409
        _scan_state.update(running=True, error=None,
                           started=datetime.now(NY).isoformat(timespec="seconds"))
    threading.Thread(target=_scan_worker, args=(mode, scope), daemon=True).start()
    return jsonify({"started": True, "mode": mode, "scope": scope}), 202


# ---- Day skill tab (opening-range path; does not change paperbot runs) ----
_skill_lock = threading.Lock()
_skill_state = {"running": False, "error": None, "board": None, "account_error": None}


def _skill_worker() -> None:
    import tools.skill_board as skill_board
    from paper.broker import Broker, BrokerError

    account = None
    account_error = None
    broker = None
    try:
        broker = Broker.from_env()
        account = broker.account()
    except BrokerError as exc:
        account_error = str(exc)
    except Exception as exc:  # noqa: BLE001 - a down broker still leaves the board readable
        account_error = str(exc)[:200]
    try:
        board = skill_board.build_board(account=account)
        with _skill_lock:
            _skill_state.update(board=board, error=None, account_error=account_error)
        if broker is not None:
            skill_board.send_passes(broker, board=board)
    except Exception as exc:  # noqa: BLE001
        with _skill_lock:
            _skill_state["error"] = str(exc)[:200]
    finally:
        with _skill_lock:
            _skill_state["running"] = False


def _skill_flat_loop() -> None:
    import time

    import market.market_calendar as market_calendar
    import tools.skill_board as skill_board
    from paper.broker import Broker, BrokerError

    wakes = 0
    while True:
        time.sleep(30)
        wakes += 1
        try:
            skill_board.flatten_due(Broker.from_env())
        except BrokerError:
            pass
        except Exception:
            import logging
            logging.getLogger("dashboard").exception("skill flat-by failed")
        if wakes % 10 != 1:
            continue
        now = datetime.now(NY)
        if not market_calendar.is_open(now):
            continue
        with _skill_lock:
            if _skill_state["running"]:
                continue
            _skill_state["running"] = True
        _skill_worker()


@app.route("/api/skill")
def api_skill():
    with _skill_lock:
        return jsonify(dict(_skill_state))


@app.route("/api/skill/run", methods=["POST"])
def api_skill_run():
    with _skill_lock:
        if _skill_state["running"]:
            return jsonify({"error": "the skill board is already checking"}), 409
        _skill_state.update(running=True, error=None)
    threading.Thread(target=_skill_worker, daemon=True).start()
    return jsonify({"started": True}), 202


if __name__ == "__main__":
    threading.Thread(target=_skill_flat_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=8000, debug=False)