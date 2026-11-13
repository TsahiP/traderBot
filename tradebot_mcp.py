"""tradebot as an MCP server: lets Claude (Desktop / Code) query this engine directly.

Read-only by design - no tool places, changes or cancels an order. Every number
comes from the same modules the bots use (analyst_context, engine, timemachine,
scanner, risk, market_levels, funda/news clients, the signal log).

Run (stdio):   .venv\\Scripts\\python.exe tradebot_mcp.py
Claude Code:   claude mcp add tradebot -- D:\\Tp\\tradebot\\.venv\\Scripts\\python.exe D:\\Tp\\tradebot\\tradebot_mcp.py
"""
from __future__ import annotations

import json
from datetime import datetime

from mcp.server.mcpserver import MCPServer

import core.config as config
import market.market_calendar as market_calendar
import core.risk as risk
from market.market_symbols import is_bybit_crypto, normalize_symbol

NY = market_calendar.NY

server = MCPServer(
    name="tradebot",
    instructions=(
        "Market analysis tools backed by the user's tradebot (yfinance/Bybit data, SEC EDGAR, "
        "local signal log). Read-only: nothing here places orders. Quote numbers exactly as returned. "
        "Times are New York time. Use analyze_symbol for a full multi-step read of one ticker, "
        "time_machine for intraday odds of a stop/target plan, risk_check before suggesting a size."
    ),
)


def _stock(symbol: str) -> str:
    sym = normalize_symbol(symbol)
    if not sym:
        raise ValueError("symbol is required")
    return sym


@server.tool()
def analyze_symbol(symbol: str, risk_usd: float | None = None, refresh: bool = True) -> dict:
    """Full analyst-room data for one ticker: fundamentals + YoY quarters, news (8-K tags),
    price action, macro + relative strength vs SPY, SMC on 1D/15M, 60-day volume profile,
    squeeze momentum, and the step-5 trade plan (entry, stop, TP1/TP2, R:R, shares)."""
    import analyst.analyst_context as analyst_context

    sym = _stock(symbol)
    holding = {"risk_usd": risk_usd} if risk_usd else None
    data = analyst_context.gather_symbol_data(sym, refresh=refresh, poll_news=refresh, holding=holding)
    return json.loads(analyst_context.build_context_json(data))


@server.tool()
def run_backtest(
    symbol: str = "SPY",
    strategy: str = "sma_crossover",
    timeframe: str = "1d",
    params: dict | None = None,
    qty: int = 10,
    capital: float = 100_000.0,
    allow_short: bool = False,
    cost_per_share: float = 0.01,
    stop_atr: float = 0.0,
    target_r: float = 0.0,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Backtest a registered strategy (sma_crossover, vwap_reversion, opening_range_breakout,
    rsi_mean_reversion). params = strategy params (e.g. {"fast": 10, "slow": 50}).
    stop_atr / target_r add bracket exits (0 = off). Returns meta, metrics (incl. profit factor,
    expectancy, Sharpe, avg R) and the last 25 trades."""
    import dashboard

    raw = {
        "symbol": symbol, "strategy": strategy, "timeframe": timeframe, "qty": qty,
        "capital": capital, "allow_short": bool(allow_short), "cost_per_share": cost_per_share,
        "stop_atr": stop_atr, "target_r": target_r, "start": start, "end": end, **(params or {}),
    }
    payload, status, error = dashboard.run_backtest_from({k: v for k, v in raw.items() if v is not None})
    if error:
        raise ValueError(f"{status}: {error}")
    return {"meta": payload["meta"], "metrics": payload["metrics"], "trades": payload["trades"][-25:]}


@server.tool()
def time_machine(
    symbol: str,
    entry: float,
    stop: float,
    target: float,
    side: str = "long",
    at: str | None = None,
    runs: int = 10_000,
    seed: int = 19,
) -> dict:
    """Monte Carlo replay of the rest of the session from 60 days of the stock's own 5m bars:
    odds of target first / stop first / neither by the close, avg R, real history on past
    sessions and a verdict. at = 'YYYY-MM-DD HH:MM' NY (default now). Stocks only."""
    import tools.timemachine as timemachine
    from market.market_data import fetch_ohlcv_period

    sym = _stock(symbol)
    if is_bybit_crypto(sym):
        raise ValueError("stocks only - the replay uses NYSE sessions")
    when = datetime.strptime(at, "%Y-%m-%d %H:%M").replace(tzinfo=NY) if at else datetime.now(NY)
    df5 = fetch_ohlcv_period(sym, "5m", "60d")
    return timemachine.replay(df5, entry=entry, stop=stop, target=target, at=when, side=side,
                              runs=min(max(runs, 100), 20_000), seed=seed)


@server.tool()
def scan_market(mode: str = "daily", scope: str = "watchlists", symbols: list[str] | None = None,
                min_price: float = 5.0, min_volume: float = 500_000) -> dict:
    """Run the setup scanner. mode: daily (20d breakout, bull flag, MA20 reclaim, oversold,
    oversold bounce) or orb (opening-range breakouts today). scope: watchlists or universe
    (~5,500 US stocks, takes minutes). Or pass explicit symbols."""
    import tools.scanner as scanner

    syms = [_stock(s) for s in symbols] if symbols else None
    res = scanner.run_scan(mode, symbols=syms, scope=scope, min_price=min_price, min_volume=min_volume)
    return {**res, "hits": res["hits"][:60]}


@server.tool()
def last_scan() -> dict:
    """The most recent scanner results (from the dashboard, CLI or scan_market)."""
    import tools.scanner as scanner

    return scanner.read_results() or {"hits": [], "note": "no scan has run yet"}


@server.tool()
def fundamentals(symbol: str, refresh: bool = False) -> dict:
    """Fundamentals snapshot (valuation, margins, balance sheet, 52w, next earnings, last
    quarters with YoY) with the provider of every field."""
    import integrations.funda_client as funda_client

    sym = _stock(symbol)
    if refresh:
        (funda_client.refresh_crypto if is_bybit_crypto(sym) else funda_client.refresh_symbol)(sym, force=True)
    return funda_client.read_snapshot(sym) or {"symbol": sym, "note": "no snapshot cached - call with refresh=true"}


@server.tool()
def news(symbol: str | None = None, limit: int = 20, refresh: bool = False) -> list[dict]:
    """Recent news for a symbol (or every watchlist symbol), newest first. SEC 8-K items carry
    tags such as earnings, leadership, deal, restatement, bankruptcy."""
    import integrations.news_client as news_client

    sym = _stock(symbol) if symbol else None
    if sym and refresh:
        news_client.poll_symbol(sym)
    return news_client.read_all_news(sym, limit=min(max(limit, 1), 100))


@server.tool()
def signal_history(limit: int = 30, symbol: str | None = None, include_filtered: bool = False) -> list[dict]:
    """Candlestick-pattern alerts the signal bot sent (entry/stop/target, confluence score,
    Time Machine odds, outcome win/loss/expired), newest first."""
    import signaling.signals as signals

    rows = signals.read_signals(limit=config.SIGNAL_MAX_LOG, include_filtered=include_filtered)
    if symbol:
        sym = _stock(symbol)
        rows = [r for r in rows if r.get("symbol") == sym]
    return rows[:min(max(limit, 1), 200)]


@server.tool()
def signal_stats() -> dict:
    """Alert outcomes by confluence score, and delivered vs blocked win rates."""
    import signaling.signals as signals

    return signals.signal_stats()


@server.tool()
def risk_check(symbol: str, entry: float, stop: float, target: float | None = None,
               qty: int | None = None, equity: float = 100_000.0, cash: float | None = None,
               entries_today: list[dict] | None = None) -> dict:
    """Run the live bot's risk gate on a long trade idea with the user's RISK_* limits.
    Without qty, sizes it (max loss, position cap, cash buffer) and reports which limit decided."""
    import livebot

    sym = _stock(symbol)
    now = datetime.now(NY)
    limits = livebot.risk_limits(now)
    cash = equity if cash is None else cash
    shares, limited_by = (qty, "given") if qty else risk.size_position(
        entry=entry, stop=stop, max_loss_usd=limits.max_loss_per_trade_usd, equity=equity, cash=cash,
        max_position_pct=limits.max_position_pct, cash_buffer_pct=limits.cash_buffer_pct)
    verdict = risk.check_entry(symbol=sym, qty=shares, entry=entry, stop=stop, target=target,
                               now_ny=now, equity=equity, cash=cash,
                               entries_today=entries_today or [], limits=limits)
    return {"verdict": verdict.summary(), "ok": verdict.ok, "reasons": verdict.reasons,
            "qty": shares, "size_limited_by": limited_by,
            "max_loss_usd": risk.max_loss(shares, entry, stop) if stop < entry else None,
            "limits": limits.__dict__}


@server.tool()
def levels(symbol: str, benchmark: str = "SPY", profile_bars: int = 60) -> dict:
    """Relative strength vs a benchmark (21/63/126/252 days) and the volume profile
    (point of control, value area) of the last `profile_bars` daily bars."""
    import market.market_levels as market_levels
    from market.market_data import fetch_ohlcv_period

    sym, bench = _stock(symbol), _stock(benchmark)
    df = fetch_ohlcv_period(sym, "1d", "2y")
    out: dict = {"symbol": sym, "benchmark": bench}
    out["relative_strength"] = market_levels.relative_strength(df, fetch_ohlcv_period(bench, "1d", "2y"))
    out["volume_profile"] = market_levels.volume_profile(df.tail(max(profile_bars, 20)))
    return out


@server.tool()
def market_status(date: str | None = None) -> dict:
    """NYSE session for a date (YYYY-MM-DD, default today): open/close times, early close,
    holiday, and whether the market is open right now."""
    now = datetime.now(NY)
    day = datetime.strptime(date, "%Y-%m-%d").date() if date else now.date()
    sess = market_calendar.session(day)
    return {
        "date": day.isoformat(),
        "trading_day": sess is not None,
        "holiday": day in market_calendar.holidays(day.year),
        "early_close": day in market_calendar.early_closes(day.year),
        "open": sess[0].strftime("%H:%M") if sess else None,
        "close": sess[1].strftime("%H:%M") if sess else None,
        "open_now": market_calendar.is_open(now) if day == now.date() else None,
        "now_ny": now.isoformat(timespec="minutes"),
    }


if __name__ == "__main__":
    server.run()
