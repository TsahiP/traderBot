# TradeBot — Handoff / Operations Guide

Multi-strategy trading bot with a local Next.js dashboard. Trades **SPY** on an
Alpaca **paper** account (never real money), backtests any ticker on demand
(daily or intraday, long or short), and shows everything on a web desk at
`http://localhost:3000`.

---

## 1. Stack

| Layer | Tech | Notes |
|---|---|---|
| Backend API | Python 3.12 · Flask | `dashboard.py`, port `8000`, API-only |
| Strategy/engine | pandas | `strategies.py` (registry) + `engine.py` — single source of truth |
| Live trading | alpaca-py (paper) | `livebot.py` |
| Historical data | yfinance | backtests, on-demand downloads |
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
                  │  │ engine.py      │  │  simulation: shorts, EOD, costs
                  │  │ strategy.py    │  │  SMA signals (livebot + SMA strat)
                  │  └────────────────┘  │
                  └──────┬───────┬───────┘
                         │       │
              livebot.py │       │ yfinance (on-demand)
              (Alpaca     │       │  daily: BACKTEST_START→now
               paper)     │       │  intraday: period-capped (7d/60d/730d)
                         │       ▼
                         ▼    output/data_*.csv (CLI only)
                  output/live_trades.csv
```

- **CLI** (`backtest.py`) and **API** (`/api/backtest/run`) call the *same*
  `engine.run_backtest()` — backtests are reproducible and identical.
- Frontend never touches Python directly; `next.config.ts` proxies
  `/api/:path*` → `http://127.0.0.1:8000` (override with env `API_ORIGIN`).

## 3. Quick start

```powershell
# one command: starts API + frontend, opens the browser
.\start-web.ps1

# or manually:
python dashboard.py        # API  :8000
cd web
npm install                # first time only
npm run dev                # UI  :3000  (or npm run build && npm start)
```

Optional envs: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (paper keys, in `.env` —
gitignored). Without them the dashboard still works on backtest data.

## 4. Project layout

```
tradebot/
├── config.py          # all knobs: SYMBOL, SMA_FAST/SLOW, QUANTITY, CAPITAL, POLL_INTERVAL_MIN
├── strategy.py        # SMA signals: compute_signals(df, fast, slow) -> signal col; latest_signal()
├── strategies.py      # STRATEGIES registry: sma_crossover, vwap_reversion, opening_range_breakout, rsi_mean_reversion
├── engine.py          # run_backtest() (shorts, EOD, costs), compute_metrics() — shared by CLI + API
├── backtest.py        # CLI: downloads data, runs engine, writes output/*.csv, prints report
├── livebot.py         # paper bot loop: market clock, daily bars, market orders, logs trades
├── dashboard.py       # Flask API (see §7 for endpoints)
├── start-web.ps1      # launches API + frontend + browser
├── requirements.txt
├── README.md          # user-facing setup
├── output/            # trades.csv, equity_curve.csv, data_<SYMBOL>.csv, live_trades.csv
├── logs/bot.log       # livebot rotating log (1 MB × 3)
└── web/               # Next.js app
    ├── next.config.ts # API proxy rewrites
    └── src/
        ├── app/       # layout (dark, fonts, Toaster), page.tsx (Dashboard | Lab tabs)
        ├── components/
        │   ├── charts/       # chart-theme.ts, equity-chart.tsx, lab-chart.tsx
        │   ├── ui/           # shadcn components (nova)
        │   └── *.tsx         # ticker-tape, stat-cards, trade-ledger, lab-form, lab-results, metrics-strip
        ├── hooks/            # use-api.ts (useLive/Stats/Trades/Equity/Strategies), use-backtest-run.ts
        └── lib/              # api.ts (typed fetcher), schemas.ts (zod), format.ts (Intl), utils.ts
```

## 5. Strategy & execution model

- **Strategies** (all in `strategies.py`, driven by the `STRATEGIES` registry):
  - `sma_crossover` — fast SMA crosses above slow → buy (+1), below → sell (−1). Any timeframe.
  - `vwap_reversion` — session-anchored VWAP; long when close deviates ≥ `deviation_pct` below it, exit when it recovers to within `exit_pct`; short the mirror. Intraday only.
  - `opening_range_breakout` — range of the first `range_minutes`; buy a close above range high, sell below range low; target `tp_mult`× range, stop `sl_mult`× range; sessions wider than `max_range_pct` of the open are skipped. Intraday only.
  - `rsi_mean_reversion` — long when RSI < oversold, exit when it crosses `exit_level`; short above overbought. Any timeframe.
- Signals are **event-based** (+1/−1/0 per bar); the engine gates positions.
- Orders execute at the **next bar's open** (no lookahead).
- Fixed quantity per trade (`qty`); optional **shorts** (`allow_short`): −1 opens a short, +1 covers; shorts need buying power at entry.
- **Costs:** `cost_per_share` is charged on every fill (commission + slippage, both sides per round trip); tracked per trade and summed in `metrics.costs_total`. Intraday backtests are meaningless without it.
- **Flat EOD:** strategies with `flat_eod` + any intraday timeframe force-close open positions at the last bar of each session (marked **EOD** on the chart, amber). Daily runs never flatten.
- Live bot still runs the SMA crossover on the **last completed daily bar**, market orders, `TimeInForce.DAY`.
- **Gotcha (fixed):** `signal` must stay boolean-typed — a `shift()`ed bool
  Series becomes `object` dtype and `~` yields truthy `-1`. Keep the
  `.fillna(False).astype(bool)` pattern in `strategy.py`.

## 6. Data files

| File | Writer | Contents |
|---|---|---|
| `output/equity_curve.csv` | `backtest.py` | OHLCV, smas, signal, order, exec, eod_exit, shares, cash, equity (tz-aware index) |
| `output/trades.csv` | `backtest.py` | closed round trips: entry/exit date+price, side, exit_type, pnl, costs |
| `output/data_<SYM>.csv` | `backtest.py` | raw OHLCV bars used by the run |
| `output/live_trades.csv` | `livebot.py` | paper round trips appended live (dashboard "LIVE" rows) |
| `logs/bot.log` | `livebot.py` | every signal/order/fill |

Note: the equity CSV has **tz-aware timestamps** (`-05:00`); `dashboard.py`
parses them with `pd.to_datetime(..., utc=True).tz_convert(None)` — keep that
if you regenerate the file differently.

## 7. API reference (`http://127.0.0.1:8000`)

| Endpoint | Params | Returns |
|---|---|---|
| `GET /api/live` | — | `{connected, market_open, symbol, close, sma_fast, sma_slow, signal, account?, position?}` — falls back to last backtest bar when keys missing |
| `GET /api/stats` | — | `{realized: {trades, total_pnl, wins, win_rate?}, backtest: {...metrics}\|null}` |
| `GET /api/trades` | — | `{trades: [{entry_date, entry_price, exit_date, exit_price, qty, pnl, source: "live"\|"backtest"}]}` newest first |
| `GET /api/equity` | — | `{dates[], equity[]}` downsampled |
| `GET /api/strategies` | — | registry `[{id, label, description, timeframes[], flat_eod, default_allow_short, default_timeframe, params[{key,label,min,max,step,default,int?,unit?}]}]` — drives the lab form |
| `GET /api/backtest/run` | `symbol` (def SPY) `strategy` (def sma_crossover) `timeframe` (def 1d) `start` `end` (optional `YYYY-MM-DD`, inclusive; intraday requests clamped to the data window) `qty` `capital` `allow_short` (`true`/`false`) `cost_per_share` + per-strategy params (`fast`,`slow`,`deviation_pct`,`exit_pct`,`range_minutes`,`tp_mult`,`sl_mult`,`max_range_pct`,`rsi_period`,`oversold`,`overbought`,`exit_level`) | `{meta, metrics{...costs_total}, series{OHLCV+smas+equity ≤800 bars}, markers[{index, date, side: buy\|sell, eod?, price}], trades[{..., side, exit_type: signal\|eod, costs}]}` |
| `POST /api/analyze` | JSON body = same params as `/api/backtest/run` | re-runs the backtest, digests results + allowed param ranges, and returns `{model, analysis}` from the local LLM (`503` when LM Studio is unreachable) |

Data windows per timeframe: `1d` → `BACKTEST_START` (2009); `1m` → 7 days;
`5m`/`15m`/`30m` → 60 days; `1h` → 730 days (yfinance caps). Intraday
requests with a `start` older than the cap are clamped to the window
(default no-range intraday run = full window, ~60 *trading* days). Daily
`end` is inclusive (backend adds one day for yfinance's exclusive end).

Errors: `400` invalid params (unknown strategy, bad timeframe for the
strategy, out-of-range params, fast ≥ slow, exit ≥ deviation, RSI ordering),
`404` unknown ticker ("No data for 'X'"), `500` engine failure. Body always
`{"error": "..."}`. Frontend validates responses with zod on every fetch.

## 8. Frontend notes

- **Hooks:** `useLive` (no polling), `useStats/useTrades/useEquity` (30 s
  polling), `useStrategies` (registry, fetched once), `useBacktestRun`
  (conditional SWR key incl. strategy/timeframe/costs params; a new run
  clears the previous chart immediately — no `keepPreviousData` —
  `run(LabValues)`), `useLlmAnalysis` (POST `/api/analyze` with the run
  params, returns `{model, analysis}`).
- **Validation:** `LabSchema` (zod) — `z.discriminatedUnion("strategy", [...])`
  with a `superRefine` for cross-field rules (fast<slow, exit<deviation,
  oversold<exit<overbought); coerce numbers; RHF typed as
  `useForm<z.input<Schema>, unknown, z.infer<Schema>>`. Form fields render
  dynamically from the `/api/strategies` param specs; switching strategy
  resets params/allow_short/timeframe via `STRATEGY_DEFAULTS`.
- **Charts:** `lightweight-charts` v5 — `chart.addSeries(CandlestickSeries,
  ...)`, `createSeriesMarkers()` (BUY/SELL arrows; EOD markers amber),
  `autoSize: true`. Intraday dates arrive as `"YYYY-MM-DD HH:MM"` (NY time),
  daily as `"YYYY-MM-DD"` — `toTime()` handles both. Theme lives in
  `chart-theme.ts` (must match CSS tokens in `globals.css`).
- **Theme:** dark terminal look — tokens in `globals.css` (`:root` light,
  `.dark`); `--up`/`--down`/`--tape`/`--amber` there if you extend colors.
  Signature element: sticky ticker tape with live pulse dot + close flash.
- **Conventions:** shadcn rules — `gap-*` not `space-y-*`, `data-icon`
  on button icons, `FieldGroup`/`Field`/`FieldError` for forms,
  semantic tokens (`text-up`, `text-down`, `bg-primary`) instead of raw colors.

## 9. Known limitations & gotchas

- Intraday edges mostly die under realistic costs — that's the point of the
  `cost_per_share` field; always run intraday with it on.
- yfinance caps intraday history (7 d for 1m, ~60 trading days for 5m/15m/30m,
  730 d for 1h) and is unofficial — it can throttle (429) or change shape.
  Use `period=` for intraday, never `start=` (start can fall just outside
  Yahoo's rolling window).
- Fixed-qty sizing means the backtest only deploys `qty × price` of capital
  (SPY @10 → ~$5k of $100k) — raise `qty` to size up.
- Backtests use `BACKTEST_START` (2009) daily; the live bot only warms up ~300 bars.
- Windows Defender can transiently lock `output/*.csv` during writes —
  retry; don't open CSVs in Excel while backtesting.
- Paper orders assume instant fills; `livebot.py` reads `filled_avg_price`
  from the order with a last-close fallback.
- Ticker symbols are user input to yfinance — a typo yields a clean 404,
  not a crash.
- `.env` holds **paper** keys only. Never add live keys; the code hardcodes
  `paper=True`.

## 10. Extending

- **New strategy:** add a signal function + an entry in `STRATEGIES`
  (`strategies.py`); the API, `/api/strategies`, form fields and zod union
  (`web/src/lib/schemas.ts`) need the matching id/params/defaults.
- **ML advisor:** built-in — run a backtest, hit **Analyze with local LLM**
  in Trade results; `/api/analyze` (dashboard.py) re-runs the test and asks
  LM Studio (`http://127.0.0.1:1234/v1`, OpenAI-compatible) for a diagnosis
  and parameter suggestions bounded by the strategy's allowed ranges.
- **Risk controls:** stop-loss / max-position / position sizing — add in
  `engine.run_backtest()` (backtest) and the `livebot.py` loop (live).
- **More tickers in the live bot:** `config.SYMBOL` today; looping a list
  means tracking per-symbol open trades in `livebot.py`.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| UI shows "API unreachable" | `dashboard.py` not running — start it first |
| Tape says "missing .env keys" | copy `.env.example` → `.env`, add paper keys |
| Lab returns 404 | ticker typo / delisted — try another symbol |
| Lab returns 400 | invalid params for the chosen strategy (fast ≥ slow, out-of-range, wrong timeframe, bad cross-field combos) |
| Lab returns 500 | engine failure — check `dashboard.py` console; usually a data quirk (zero-volume bar) |
| Ledger empty | no backtest run yet (run `backtest.py`) and no closed paper trades |
| `next build` fails on unknown utility | check `globals.css` for stale arbitrary classes |
| Ports busy | 3000/8000 in use — kill with `Stop-Process` or change port in `dashboard.py` / `npm run dev -- -p 3001` |

## 12. Current state (last verified)

- Backtest SPY SMA 10/50 qty 10, daily: **+4.08%** (2009→2026-08), 56 trades,
  57.1% win rate, −0.64% max DD; buy & hold +1046%.
- All 4 strategies verified via the API: SMA (1d/1m), VWAP reversion (5m),
  ORB (5m), RSI reversion (15m) on SPY/AAPL — markers match fills (no phantom
  signals), EOD flattens marked, costs tracked per trade.
- Open positions at the end of the data are reported as a row with
  `exit_type: "open"` (exit fields `null`, P&L unrealized, `*` in the UI) —
  every chart marker now has a table row.
- **LLM advisor live:** `POST /api/analyze` re-runs the last lab test and has
  the local LM Studio model return Diagnosis / Suggested configurations /
  Risks (hermes-3-llama-3.1-8b-lorablated auto-picked — the 27b Qwen is far
  too slow on this machine; override with `LLM_MODEL` in `.env`, base URL
  via `LLM_BASE_URL`).
- Error paths verified: unknown strategy → 400, wrong timeframe → 400,
  fast ≥ slow / exit ≥ deviation / RSI ordering → 400, LM Studio down → 503.
- Full stack smoke-tested: page 200, `/api/strategies`, `/api/live`,
  `/api/backtest/run` 200 via the Next proxy.
- `npm run lint` (1 benign React-Compiler/RHF `watch` warning), `npm run build` green.