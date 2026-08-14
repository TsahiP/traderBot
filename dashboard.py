"""Local API for the trade bot.

Run:
    python dashboard.py
The Next.js frontend (web/) proxies /api/* to this server (port 8000).
"""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, jsonify, request

import config
import engine
from livebot import get_last_completed_bars
from strategy import compute_signals, latest_signal

load_dotenv(config.BASE_DIR / ".env")
NY = ZoneInfo("America/New_York")

app = Flask(__name__)

TRADES_FILE = config.OUTPUT_DIR / "live_trades.csv"
TRADES_BT_FILE = config.OUTPUT_DIR / "trades.csv"
EQUITY_FILE = config.OUTPUT_DIR / "equity_curve.csv"

MAX_SERIES_BARS = 800


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

    markers = []
    order_idx = curve.index[curve["order"] != 0].tolist()
    for i, orig in enumerate(order_idx):
        row = curve.loc[orig]
        markers.append({
            "index": pos_of(curve.index.get_loc(orig)),
            "date": fmt_ts(orig),
            "side": "buy" if row["order"] == 1 else "sell",
            "price": round(float(row["Open"]), 2),
        })

    return {
        "series": {
            "dates": [str(d.date()) for d in sub.index],
            "open": [round(float(v), 2) for v in sub["Open"]],
            "high": [round(float(v), 2) for v in sub["High"]],
            "low": [round(float(v), 2) for v in sub["Low"]],
            "close": [round(float(v), 2) for v in sub["Close"]],
            "volume": [int(v) for v in sub["Volume"]],
            "sma_fast": [round(float(v), 2) if not np.isnan(v) else None for v in sub["sma_fast"]],
            "sma_slow": [round(float(v), 2) if not np.isnan(v) else None for v in sub["sma_slow"]],
            "equity": [round(float(v), 2) for v in sub["equity"]],
        },
        "markers": markers,
        "metrics": engine.compute_metrics(curve, trades_df, capital),
        "trades": [
            {
                "entry_date": fmt_ts(r["entry_date"]),
                "entry_price": round(float(r["entry_price"]), 2),
                "exit_date": fmt_ts(r["exit_date"]),
                "exit_price": round(float(r["exit_price"]), 2),
                "pnl": round(float(r["pnl"]), 2),
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


@app.route("/api/backtest/run")
def api_backtest_run():
    raw = request.args
    try:
        symbol = (raw.get("symbol") or config.SYMBOL).strip().upper()
        fast = int(raw.get("fast") or config.SMA_FAST)
        slow = int(raw.get("slow") or config.SMA_SLOW)
        qty = int(raw.get("qty") or config.QUANTITY)
        capital = float(raw.get("capital") or config.CAPITAL)
    except ValueError:
        return jsonify({"error": "Invalid parameter values"}), 400

    if not symbol or len(symbol) > 12 or not symbol.replace(".", "").isalnum():
        return jsonify({"error": "Invalid symbol - use e.g. SPY, AAPL, BTC-USD"}), 400
    if fast < 1 or slow < 2 or fast >= slow:
        return jsonify({"error": "SMA periods must satisfy 1 <= fast < slow"}), 400
    if qty < 1 or qty > 100_000 or capital <= 0:
        return jsonify({"error": "qty must be 1-100000 and capital > 0"}), 400

    import yfinance as yf

    df = yf.Ticker(symbol).history(
        start=config.BACKTEST_START, auto_adjust=True, actions=False
    )
    if df.empty:
        return jsonify({"error": f"No data for '{symbol}' - check the ticker symbol"}), 404

    curve, trades_df = engine.run_backtest(df, fast, slow, qty, capital)
    payload = build_run_payload(curve, trades_df, capital)
    payload["meta"] = {"symbol": symbol, "fast": fast, "slow": slow, "qty": qty, "capital": capital}
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)