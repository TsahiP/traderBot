# TradeBot — Handoff / Operations Guide

Multi-strategy trading bot with a local Next.js dashboard. Trades **SPY** on an
Alpaca **paper** account (never real money), backtests any ticker on demand
(daily or intraday, long or short), and shows everything on a web desk at
`http://localhost:3000`.

---

## 1. Stack

| Layer | Tech | Notes |
|---|---|---|
| Backend API | Python 3.11/3.12 · Flask | `dashboard.py`, port `8000`, API-only |
| Strategy/engine | pandas · numpy | `strategies.py` (registry) + `engine.py` — single source of truth |
| Live trading | alpaca-py (paper) | `livebot.py` + `alpaca_service.py` |
| Historical data | yfinance | backtests, on-demand downloads |
| Test suite | pytest | `tests/` — 20 unit & integration tests |
| Local LLM advisor | LM Studio (OpenAI-compatible) | `/api/analyze` re-runs a backtest and has the local model critique it |
| Frontend | Next.js 16 (App Router) · TypeScript · Tailwind v4 | `web/`, port `3000` |
| UI kit | shadcn/ui (nova preset, Base UI) | components in `web/src/components/ui/` |
| Data layer | SWR (custom hooks) · zod (validation) | schemas in `web/src/lib/schemas.ts` |
| Charts | lightweight-charts (TradingView) v5 | candles + overlays + buy/sell/EOD markers, equity area |

## 2. Architecture

```
┌────────────┐  /api/*  ┌──────────────────┐
│  Browser   │ ───────► │  Next.js :3000   │  rewrites /api/* → Flask
│  :3000     │ ◄─────── │  (web/)          │
└────────────┘          └────────┬─────────┘
                                 ▼
                  ┌──────────────────────┐
                  │  Flask API :8000     │  dashboard.py
                  │  ┌────────────────┐  │
                  │  │ strategies.py  │  │  4-strategy registry (signals)
                  │  │ engine.py      │  │  simulation: shorts, EOD, linear costs
                  │  │ strategy.py    │  │  SMA signals (livebot + SMA strat)
                  │  │ alpaca_service │  │  shared Alpaca client & bar handling
                  │  └────────────────┘  │
                  └──────┬───────┬───────┘
                         │       │
              livebot.py │       │ yfinance (on-demand)
              (Alpaca    │       │  daily: BACKTEST_START→now
               paper)    │       │  intraday: period-capped (7d/60d/730d)
                         │       ▼
                         ▼    output/data_*.csv (CLI only)
                  output/live_trades.csv
```

- **CLI** (`backtest.py`) and **API** (`/api/backtest/run`) call the *same*
  `engine.run_backtest()` — backtests are reproducible and identical.
- Frontend never touches Python directly; `next.config.ts` proxies
  `/api/:path*` → `http://127.0.0.1:8000` (override with env `API_ORIGIN`).
- Tests in `tests/` run via `pytest` and verify accounting, order execution, edge cases, and API routes.

## 3. Quick start

```powershell
# one command: starts API + frontend, opens the browser
.\start-web.ps1

# or manually:
python dashboard.py        # API  :8000
cd web
npm install                # first time only
npm run dev                # UI  :3000  (or npm run build && npm start)

# run tests:
pytest -v
```

Optional envs: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (paper keys, in `.env` —
gitignored). `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` enable trade and error
alerts from `livebot.py` (see § Notifications). Without Alpaca keys the dashboard
still works on backtest data.

## 4. Project layout

```
tradebot/
├── config.py             # all knobs: SYMBOL, SMA_FAST/SLOW, QUANTITY, CAPITAL, POLL_INTERVAL_MIN
├── alpaca_service.py     # shared Alpaca client initialization, snapshots, fill polling & bar normalization
├── strategy.py           # SMA signals: compute_signals(df, fast, slow) -> signal col; latest_signal()
├── strategies.py         # STRATEGIES registry: sma_crossover, vwap_reversion, opening_range_breakout, rsi_mean_reversion
├── engine.py             # run_backtest() (shorts, EOD, linear costs), compute_metrics() — shared by CLI + API
├── backtest.py           # CLI: downloads data, runs engine, writes output/*.csv, prints report
├── livebot.py            # paper bot loop: market clock, daily bars, market orders, logs trades
├── notifier.py           # optional Telegram alerts (trade fills, errors, heartbeat)
├── dashboard.py          # Flask API (see §7 for endpoints)
├── start-web.ps1         # launches API + frontend + browser
├── requirements.txt
├── tests/                # Automated pytest suite
│   ├── test_engine.py
│   ├── test_strategies.py
│   ├── test_dashboard_api.py
│   ├── test_notifier.py
│   └── test_alpaca_service.py
├── output/               # trades.csv, equity_curve.csv, data_<SYMBOL>.csv, live_trades.csv
├── logs/
│   ├── bot.log           # livebot rotating log (1 MB × 3)
│   └── dashboard.log     # API server rotating log (1 MB × 3)
└── web/                  # Next.js app
    ├── next.config.ts    # API proxy rewrites
    └── src/
        ├── app/          # layout (dark, fonts, Toaster), page.tsx (Dashboard | Lab tabs)
        ├── components/
        │   ├── charts/   # chart-theme.ts, equity-chart.tsx, lab-chart.tsx
        │   ├── ui/       # shadcn components (nova)
        │   └── *.tsx     # ticker-tape, stat-cards, trade-ledger, lab-form, lab-results, metrics-strip
        ├── hooks/        # use-api.ts (useLive/Stats/Trades/Equity/Strategies), use-backtest-run.ts
        └── lib/          # api.ts (typed fetcher), schemas.ts (zod), format.ts (Intl), utils.ts
```

## 5. Strategy & execution model

- **Strategies** (all in `strategies.py`, driven by the `STRATEGIES` registry):
  - `sma_crossover` — fast SMA crosses above slow → buy (+1), below → sell (−1). Supports long-only and long/short reversal. Any timeframe.
  - `vwap_reversion` — session-anchored VWAP; long when close deviates ≥ `deviation_pct` below it, exit when it recovers to within `exit_pct`; short the mirror (when `allow_short=True`). Intraday only. Zero-volume bar resilient.
  - `opening_range_breakout` — range of the first `range_minutes`; buy a close above range high, sell below range low (when `allow_short=True`); target `tp_mult`× range, stop `sl_mult`× range; sessions wider than `max_range_pct` of the open are skipped. Intraday only.
  - `rsi_mean_reversion` — long when RSI < oversold, exit when it crosses `exit_level`; short above overbought (when `allow_short=True`). Any timeframe.
- Signals are **event-based** (+1/−1/0 per bar); the engine gates positions and respects `allow_short`.
- Orders execute at the **next bar's open** (no lookahead).
- Fixed quantity per trade (`qty`); optional **shorts** (`allow_short`): −1 opens a short, +1 covers; shorts need buying power at entry.
- **Costs:** `cost_per_share` is charged linearly on every fill (`cost_per_share * qty` per leg, `2 * cost_per_share * qty` per round trip); tracked per trade and summed in `metrics.costs_total`.
- **Flat EOD:** strategies with `flat_eod` + any intraday timeframe force-close open positions at the last bar of each session (marked **EOD** on the chart, amber). Daily runs never flatten.
- Live bot runs the SMA crossover on the **last completed daily bar**, market orders, `TimeInForce.DAY`. Synchronizes with existing open positions on startup.

## 6. Data files

| File | Writer | Contents |
|---|---|---|
| `output/equity_curve.csv` | `backtest.py` | OHLCV, smas, signal, order, exec, eod_exit, shares, cash, equity (tz-aware index) |
| `output/trades.csv` | `backtest.py` | closed round trips: entry/exit date+price, side, exit_type, pnl, costs |
| `output/data_<SYM>.csv` | `backtest.py` | raw OHLCV bars used by the run |
| `output/live_trades.csv` | `livebot.py` | paper round trips appended live (dashboard "LIVE" rows) |
| `logs/bot.log` | `livebot.py` | every signal/order/fill |
| `logs/dashboard.log` | `dashboard.py` | API server requests & errors |

## 7. API reference (`http://127.0.0.1:8000`)

| Endpoint | Params | Returns |
|---|---|---|
| `GET /api/live` | — | `{connected, market_open, symbol, close, sma_fast, sma_slow, signal, account?, position?}` — falls back to last backtest bar when keys missing |
| `GET /api/stats` | — | `{realized: {trades, total_pnl, wins, win_rate?}, backtest: {...metrics}\|null}` |
| `GET /api/trades` | — | `{trades: [{entry_date, entry_price, exit_date, exit_price, qty, pnl, costs, source: "live"\|"backtest"}]}` newest first (safe sort with open trades) |
| `GET /api/equity` | — | `{dates[], equity[]}` downsampled |
| `GET /api/strategies` | — | registry `[{id, label, description, timeframes[], flat_eod, default_allow_short, default_timeframe, params[{key,label,min,max,step,default,int?,unit?}]}]` |
| `GET /api/backtest/run` | `symbol` `strategy` `timeframe` `start` `end` `qty` `capital` `allow_short` `cost_per_share` + params | `{meta, metrics{...costs_total}, series{OHLCV+smas+equity ≤800 bars}, markers, trades}` |
| `POST /api/analyze` | JSON body = same params as `/api/backtest/run` | re-runs the backtest, digests results + allowed param ranges, and returns `{model, analysis}` from the local LLM (`503` when LM Studio is unreachable) |

## 8. Notifications

Optional Telegram alerts and interactive commands run with `livebot.py`.
`notifier.py` sends fail-safe messages and `telegram_bot.py` long-polls for
commands. Telegram failures are logged and never crash the trading loop.

### Setup
1. Open Telegram and message **@BotFather** → `/newbot` → copy the bot token into `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find `"chat":{"id":...}` — that number is `TELEGRAM_CHAT_ID`.
4. Add both to `.env`. Restart `livebot.py`.

Only the configured chat ID is authorized; other chats receive no account or
operational information.

### Commands

| Command | Behavior |
|---|---|
| `/status` | Bot uptime/health, market state, equity, buying power, and open positions |
| `/pnl` | Alpaca account day P&L (`equity - last_equity`) and aggregate open-position unrealized P&L |
| `/pause` | Blocks new entries while strategy exits and position synchronization continue |
| `/resume` | Re-enables new entries |
| `/backtest <SYMBOL> <STRATEGY>` | Runs one asynchronous backtest using strategy defaults |
| `/help` | Shows command syntax and strategy IDs |

Supported strategy IDs are `sma_crossover`, `vwap_reversion`,
`opening_range_breakout`, and `rsi_mean_reversion`. Only one Telegram
backtest runs at a time. Compact metrics are returned without chart data.

The listener exists only while `livebot.py` is running. Pause state is
in-memory and safely resets to active after every process restart; the startup
message explicitly reports `ACTIVE`.

## 9. QA & Bug Fix Summary (2026-08-28)

1. **Fixed Quadratic Cost Accounting in `engine.py`**:
   - Normalized `cost_per_trade = cost_per_share * qty`. Fixed cash deductions and trade PnL calculations so costs are strictly linear.
2. **Fixed Short Entry/Cover Collisions in `strategies.py`**:
   - `vwap_reversion`, `opening_range_breakout`, and `rsi_mean_reversion` now accept `allow_short`. When `allow_short=False`, they strictly ignore short triggers and never emit erroneous cover buys.
3. **Fixed SMA Crossover Reversal Position Flipping**:
   - When `allow_short=True`, a death cross now closes long and immediately enters short; a golden cross closes short and enters long.
4. **Fixed VWAP Zero-Volume Bar NaN Corruption**:
   - VWAP calculation now uses robust zero-safe cumulative divisions with forward/backward fills.
5. **Fixed Wilders RSI Calculation on Pure Trend**:
   - Fixed divide-by-zero handling in RSI calculation so pure uptrends register RSI=100 and pure downtrends register RSI=0.
6. **Fixed Live Bot Open Position on Startup**:
   - `livebot.py` now queries Alpaca on startup to populate `open_trade` if an open position already exists.
7. **Fixed Order Fill Polling in `livebot.py`**:
   - `alpaca_service.fetch_fill_price` retries up to 3 times before falling back to `last_close`.
8. **Fixed `/api/trades` and `/api/stats`**:
   - Safely sorts trades without crashing when `exit_date` is `None` (open positions). Carries `costs` column into backtest stats.
9. **Added Full Test Suite**:
   - 20 unit and integration tests in `tests/` covering engine, strategies, API routes, notifier, and Alpaca service.
