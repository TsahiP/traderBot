# TradeBot — Handoff / Operations Guide

SMA-crossover trading bot with a local Next.js dashboard. Trades **SPY** on an
Alpaca **paper** account (never real money), backtests any ticker on demand,
and shows everything on a web desk at `http://localhost:3000`.

---

## 1. Stack

| Layer | Tech | Notes |
|---|---|---|
| Backend API | Python 3.12 · Flask | `dashboard.py`, port `8000`, API-only |
| Strategy/engine | pandas | `strategy.py` + `engine.py` — single source of truth |
| Live trading | alpaca-py (paper) | `livebot.py` |
| Historical data | yfinance | backtests, on-demand downloads |
| Frontend | Next.js 16 (App Router) · TypeScript · Tailwind v4 | `web/`, port `3000` |
| UI kit | shadcn/ui (nova preset, Base UI) | components in `web/src/components/ui/` |
| Data layer | SWR (custom hooks) · zod (validation) | schemas in `web/src/lib/schemas.ts` |
| Charts | lightweight-charts (TradingView) v5 | candles + SMA + buy/sell markers, equity area |

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
                  │  │ engine.py      │  │  backtest simulation
                  │  │ strategy.py    │  │  SMA crossover signals
                  │  └────────────────┘  │
                  └──────┬───────┬───────┘
                         │       │
              livebot.py │       │ yfinance (on-demand)
              (Alpaca     │       │
               paper)     │       ▼
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
├── strategy.py        # compute_signals(df, fast, slow) -> signal col; latest_signal()
├── engine.py          # run_backtest(), compute_metrics() — shared by CLI + API
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
        ├── hooks/            # use-api.ts (useLive/Stats/Trades/Equity), use-backtest-run.ts
        └── lib/              # api.ts (typed fetcher), schemas.ts (zod), format.ts (Intl), utils.ts
```

## 5. Strategy & execution model

- Signal: fast SMA crosses **above** slow SMA → buy (+1); crosses below → sell (−1); else 0.
- Orders execute at the **next bar's open** (no lookahead).
- Fixed quantity per trade (`QUANTITY`); position gate prevents double orders.
- Live bot acts on the **last completed daily bar** (drops the in-progress
  bar while the market is open), market orders, `TimeInForce.DAY`.
- **Gotcha (fixed):** `signal` must stay boolean-typed — a `shift()`ed bool
  Series becomes `object` dtype and `~` yields truthy `-1`. Keep the
  `.fillna(False).astype(bool)` pattern in `strategy.py`.

## 6. Data files

| File | Writer | Contents |
|---|---|---|
| `output/equity_curve.csv` | `backtest.py` | OHLCV, smas, signal, order, shares, cash, equity (tz-aware index) |
| `output/trades.csv` | `backtest.py` | closed round trips: entry/exit date+price, pnl |
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
| `GET /api/backtest/run` | `symbol` (def SPY) `fast` `slow` `qty` `capital` | `{meta, metrics, series{OHLCV+smas+equity ≤800 bars}, markers[{index, date, side, price}], trades[]}` |

Errors: `400` invalid params (fast ≥ slow, bad symbol chars), `404` unknown
ticker ("No data for 'X'"), `500` engine failure. Body always
`{"error": "..."}`. Frontend validates responses with zod on every fetch.

## 8. Frontend notes

- **Hooks:** `useLive` (no polling), `useStats/useTrades/useEquity` (30 s
  polling), `useBacktestRun` (conditional SWR key, `keepPreviousData` while
  re-running; `run(LabValues)`).
- **Validation:** `LabSchema` (zod) — symbol chars/len, fast<slow, qty/capital
  ranges; coerce numbers; RHF typed as `useForm<z.input<Schema>, unknown, z.infer<Schema>>`.
- **Charts:** `lightweight-charts` v5 — `chart.addSeries(CandlestickSeries,
  ...)`, `createSeriesMarkers()`, `autoSize: true`. Theme lives in
  `chart-theme.ts` (must match CSS tokens in `globals.css`).
- **Theme:** dark terminal look — tokens in `globals.css` (`:root` light,
  `.dark`); add `--up`/`--down`/`--tape` there if you extend colors.
  Signature element: sticky ticker tape with live pulse dot + close flash.
- **Conventions:** shadcn rules — `gap-*` not `space-y-*`, `data-icon`
  on button icons, `FieldGroup`/`Field`/`FieldError` for forms,
  semantic tokens (`text-up`, `text-down`, `bg-primary`) instead of raw colors.

## 9. Known limitations & gotchas

- No commission/slippage modeling; fixed-qty sizing means the backtest only
  deploys `QUANTITY × price` of capital (SPY @10 → ~$5k of $100k) — raise
  `QUANTITY` to size up.
- Backtests use `BACKTEST_START` (2009); the live bot only warms up ~300 bars.
- Windows Defender can transiently lock `output/*.csv` during writes —
  retry; don't open CSVs in Excel while backtesting.
- Paper orders assume instant fills; `livebot.py` reads `filled_avg_price`
  from the order with a last-close fallback.
- Ticker symbols are user input to yfinance — a typo yields a clean 404,
  not a crash.
- `.env` holds **paper** keys only. Never add live keys; the code hardcodes
  `paper=True`.

## 10. Extending

- **New strategy:** rewrite `strategy.py` — CLI, API, livebot and dashboard
  all pick it up automatically (keep the signal contract: +1/−1/0).
- **ML advisor:** `strategy.py` can call LM Studio
  (`http://127.0.0.1:1234/v1/chat/completions`, OpenAI-compatible) to
  influence signals — same endpoint the user already runs for Qwen locally.
- **Risk controls:** stop-loss / max-position / slippage — add in
  `engine.run_backtest()` (backtest) and the `livebot.py` loop (live).
- **More tickers in the live bot:** `config.SYMBOL` today; looping a list
  means tracking per-symbol open trades in `livebot.py`.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| UI shows "API unreachable" | `dashboard.py` not running — start it first |
| Tape says "missing .env keys" | copy `.env.example` → `.env`, add paper keys |
| Lab returns 404 | ticker typo / delisted — try another symbol |
| Lab returns 400 | fast ≥ slow, or out-of-range qty/capital |
| Ledger empty | no backtest run yet (run `backtest.py`) and no closed paper trades |
| `next build` fails on unknown utility | check `globals.css` for stale arbitrary classes |
| Ports busy | 3000/8000 in use — kill with `Stop-Process` or change port in `dashboard.py` / `npm run dev -- -p 3001` |

## 12. Current state (last verified)

- Backtest SPY SMA 10/50 qty 10: **+4.07%** (2009→2026-08), 56 trades,
  57.1% win rate, −0.64% max DD; buy & hold +1046%.
- Full stack smoke-tested: page 200, `/api/live`, `/api/stats`,
  `/api/backtest/run` (SPY, AAPL) 200 via the Next proxy.
- `npm run lint` clean, `npm run build` green.