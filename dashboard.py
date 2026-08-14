"""Local API for the trade bot.

Run:
    python dashboard.py
The Next.js frontend (web/) proxies /api/* to this server (port 8000).
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, request

import config
import engine
import strategies
from livebot import get_last_completed_bars
from strategy import compute_signals, latest_signal

load_dotenv(config.BASE_DIR / ".env")
NY = ZoneInfo("America/New_York")

app = Flask(__name__)

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
        bars = get_last_completed_bars(data)
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
    trades = load_live_trades() + load_backtest_trades()
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


@app.route("/api/backtest/run")
def api_backtest_run():
    raw = request.args
    try:
        symbol = (raw.get("symbol") or config.SYMBOL).strip().upper()
        strategy_id = (raw.get("strategy") or "sma_crossover").strip()
        timeframe = (raw.get("timeframe") or "1d").strip()
        qty = int(raw.get("qty") or config.QUANTITY)
        capital = float(raw.get("capital") or config.CAPITAL)
        allow_short = (raw.get("allow_short") or "false").lower() in ("1", "true", "yes")
        cost_per_share = float(raw.get("cost_per_share") or 0.0)
    except ValueError:
        return jsonify({"error": "Invalid parameter values"}), 400

    if not symbol or len(symbol) > 12 or not symbol.replace(".", "").isalnum():
        return jsonify({"error": "Invalid symbol - use e.g. SPY, AAPL, BTC-USD"}), 400
    if strategy_id not in strategies.STRATEGIES:
        return jsonify({"error": f"Unknown strategy '{strategy_id}'"}), 400
    spec = strategies.STRATEGIES[strategy_id]
    if timeframe not in spec["timeframes"]:
        return jsonify({"error": f"'{timeframe}' not supported by {spec['label']}"}), 400
    if qty < 1 or qty > 100_000 or capital <= 0:
        return jsonify({"error": "qty must be 1-100000 and capital > 0"}), 400
    if cost_per_share < 0 or cost_per_share > 1:
        return jsonify({"error": "cost_per_share must be $0-$1"}), 400
    try:
        params = strategies.parse_params(spec, raw)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if strategy_id == "sma_crossover" and params["fast"] >= params["slow"]:
        return jsonify({"error": "SMA periods must satisfy fast < slow"}), 400
    if strategy_id == "vwap_reversion" and params["exit_pct"] >= params["deviation_pct"]:
        return jsonify({"error": "Exit level must be below the entry deviation"}), 400
    if strategy_id == "rsi_mean_reversion" and not (
        params["oversold"] < params["exit_level"] < params["overbought"]
    ):
        return jsonify({"error": "Need oversold < exit level < overbought"}), 400

    import yfinance as yf

    try:
        start_d = parse_date(raw.get("start"), "start date")
        end_d = parse_date(raw.get("end"), "end date")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if start_d and end_d and start_d > end_d:
        return jsonify({"error": "Start date must be on or before the end date"}), 400

    kwargs: dict = {"auto_adjust": True, "actions": False}
    if timeframe == "1d":
        kwargs["start"] = (start_d or config.BACKTEST_START).isoformat()
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

    df = yf.Ticker(symbol).history(**kwargs)
    if df.empty:
        return jsonify({"error": f"No data for '{symbol}' - check the ticker symbol"}), 404

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
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)