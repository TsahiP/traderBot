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
| Signal alerts | yfinance or Bybit + bar-close scheduler + matplotlib | `signalbot.py` — candlestick patterns → Telegram + optional Discord (text + chart image). Tickers ending in `-USD` use Bybit spot |
| Historical data | yfinance; Bybit spot for `*-USD` | backtests, on-demand downloads. `BTC`/`ETH` alias to `BTC-USD`/`ETH-USD` |
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
              livebot.py │       │ yfinance (stocks) / Bybit spot (*-USD)
              (Alpaca     │       │  daily: BACKTEST_START→now
               paper)     │       │  intraday: period-capped (7d/60d/730d)
                          │       ▼
                          ▼    output/data_*.csv (CLI only)
                   output/live_trades.csv

 signalbot.py ── yfinance (stocks) or Bybit spot (*-USD) per (watchlist × symbol × timeframe) when each bar closes; config re-read each scheduler wake
   candle_patterns.detect_all() on last closed bar → Telegram and/or Discord text + PNG chart (per watchlist channel TFs)
   state: output/signal_config.json · signal_log.json (dedupe) · signal_heartbeat.json
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
gitignored). Without them the dashboard still works on backtest data. Crypto
bars (`BTC-USD`, or `BTC`/`ETH` which alias to it): `BYBIT_API_KEY`,
`BYBIT_SECRET_KEY` — market data only, no Bybit orders. Signal bot:
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`; optional Discord webhooks per stock/crypto
timeframe and news (`DISCORD_WEBHOOK_STOCK_*`, `DISCORD_WEBHOOK_CRYPTO_*`,
`DISCORD_WEBHOOK_*_NEWS`; legacy `DISCORD_WEBHOOK_*_TRADE` still backs stock routes).
`DISCORD_BOT_TOKEN` + `DISCORD_GUILD_ID` enable **Sync from Discord** (channel names
`stock-1h`, `crypto-5m`, `stock-news`, … → `output/discord_webhooks.json`; env URLs
override synced file).

## 4. Project layout

```
tradebot/
├── config.py          # all knobs: SYMBOL, SMA_FAST/SLOW, QUANTITY, CAPITAL, POLL_INTERVAL_MIN
├── strategy.py        # SMA signals: compute_signals(df, fast, slow) -> signal col; latest_signal()
├── strategies.py      # STRATEGIES registry: sma_crossover, vwap_reversion, opening_range_breakout, rsi_mean_reversion
├── engine.py          # run_backtest() (shorts, EOD, costs), compute_metrics() — shared by CLI + API
├── market_symbols.py  # normalize tickers; `-USD` means Bybit spot (`BTC-USD` → `BTCUSDT`)
├── bybit_client.py    # signed Bybit v5 spot klines (no orders)
├── market_data.py     # fetch_ohlcv(): Bybit for `*-USD`, yfinance otherwise
├── backtest.py        # CLI: downloads data, runs engine, writes output/*.csv, prints report
├── livebot.py         # paper bot loop: market clock, daily bars, risk-gated bracket entries (ATR stop + R target, GTC), broker-exit reconcile, state file
├── market_calendar.py # rule-based NYSE calendar: holidays(year), early_closes(year), session(d), is_open(dt), stock_bar_due() (signalbot skips closed-market stock scans)
├── timemachine.py     # Monte Carlo replay of the rest of a session from 60d of 5m bars: replay() odds/avg R/history/verdict, format_line()
├── market_levels.py   # relative_strength(df, bench) vs SPY; volume_profile(df) POC + value area (volume spread by overlap)
├── scanner.py         # setup scanner: Nasdaq Trader universe, daily setups (20d breakout, bull flag, MA20 reclaim, oversold, bounce), ORB; output/scan_results.json
├── tradebot_mcp.py    # MCP server (mcp SDK 2.x MCPServer, stdio): 12 read-only tools over the engine — never places orders
├── risk.py            # pure entry gate: check_entry() -> PASS/BLOCK with reasons (stop, loss, R:R, daily loss, trades/day, duplicate, NY window, position cap, cash buffer)
├── signalbot.py       # signal bot loop: poll yfinance or Bybit per config, detect patterns, optional confluence gate, alert Telegram (text + chart PNG)
├── confirmations.py   # optional confluence checks (EMA 200, volume, RSI, MACD, EMA 50 / Bollinger) → score
├── signal_outcomes.py # after each wake, mark logged alerts win / loss / expired from bars already fetched
├── candle_patterns.py # 6 strong Japanese-candlestick detectors + PATTERNS registry {id: label/direction/bars}
├── squeeze.py         # LazyBear Squeeze Momentum (BB 20/2 + KC 20/1.5 linreg histogram): momentum_series(), squeeze_snapshot(), bar colors
├── smc.py             # Smart Money Concepts, a port of LuxAlgo SMC (LUXALGO_SETTINGS: swing 50 / internal 5, 5 internal OBs ATR+High/Low, EQH/EQL 3·0.1, FVG auto+extend 5): run_luxalgo(df) state machine, detect_smc(window) chart layers, snapshot_smc(df) dict
├── news_client.py     # multi-provider news fetch + merged feed for a symbol; geopolitical_headlines() for the macro step
├── market_context.py  # macro block for the analyst room: QQQ regime, VIX, oil (CL/BZ), ESLT, geopolitics; TTL cache (MACRO_TTL_MIN)
├── trade_plan.py      # step-5 execution math: round_break(), position_size(), build_trade_plan(price, smc, atr, risk_usd, holding)
├── analysis_charts.py # render_analysis_chart() — dark 1D/15M PNG with SMC overlay + Premium/EQ/Discount lines + squeeze sub-panel
├── analyst_context.py # analyst-room data assembly + LLM prompt: gather_symbol_data(), build_context_json(), analysis_messages(), run_analysis()
├── signals.py         # shared state for the Signals feature: config load/save/validate, signal log + dedupe keys, heartbeat, Telegram + Discord webhook send (text/photo)
├── discord_channel_routes.py  # Discord channel/category name → canonical route (stock_1h, …)
├── discord_sync.py    # guild channel scan → output/discord_webhooks.json
├── chart_image.py     # render_candles() — dark-theme PNG: candles + alert bar highlight + long/short entry/stop/target overlay
├── llm_client.py      # local LM Studio (OpenAI-compatible) chat helper: strip reasoning tags, trim context, vision_user_message() for image inputs
├── analystbot.py      # Discord analyst room: locked fundi-analyst channel + pinned ticker select + position modal -> gather -> LLM -> chunked posts + chart files
├── dashboard.py       # Flask API (see §7 for endpoints)
├── start-web.ps1      # launches API + frontend + browser
├── requirements.txt
├── README.md          # user-facing setup
├── output/            # trades.csv, equity_curve.csv, data_<SYMBOL>.csv, live_trades.csv, signal_config.json, signal_log.json, signal_heartbeat.json
├── logs/bot.log       # livebot rotating log (1 MB × 3)
├── logs/signalbot.log # signal bot: wake/idle per cycle, per-scan lines, SIGNAL + dedupe/delivery
└── web/               # Next.js app
    ├── next.config.ts # API proxy rewrites
    └── src/
        ├── app/       # layout (dark, fonts, Toaster), page.tsx (Dashboard | Lab | Signals tabs)
        ├── components/
        │   ├── charts/       # chart-theme.ts, equity-chart.tsx, lab-chart.tsx
        │   ├── signals/      # signals-panel, watchlist-form-card, side column, history table, utils
        │   ├── ui/           # shadcn components (nova)
        │   └── *.tsx         # ticker-tape, stat-cards, trade-ledger, lab-form, lab-results, metrics-strip
        ├── hooks/            # use-api.ts (useLive/Stats/Trades/Equity/Strategies), use-backtest-run.ts, use-signals.ts
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
- Live bot still runs the SMA crossover on the **last completed daily bar**,
  but every entry is an Alpaca **bracket** (market buy, GTC stop-loss at
  `STOP_ATR_MULT` × ATR(14), GTC take-profit at `TARGET_R` R), sent only after
  `risk.check_entry()` passes (`RISK_*` in `config.py`). Each crossover bar is
  entered once (`acted_signal_bar`), so a stop-out does not re-buy the same
  signal. A sell signal cancels the open legs, then market-sells the position
  qty. Exits are never risk-gated. With `RISK_SIZE_BY_RISK` the qty is
  `risk.size_position()` (tightest of max loss / position cap / cash buffer,
  capped at `QUANTITY`); 0 shares blocks with `size:`. On an NYSE early close
  the entry window ends 3h sooner.
- **Bracket exits in the backtest:** `run_backtest(stop_atr_mult, target_r)`
  — stop = entry ∓ mult × ATR(14) of the bar *before* the entry, target =
  entry ± R × risk; checked inside every bar from the entry bar on; a gap
  through a level fills at the open; a bar touching both = stop. Exits are
  `exit_type: stop|target` with `r_multiple`, and chart markers carry
  `exit: stop|target` (curve columns `bracket_exit/kind/price`). `backtest.py`
  uses `STOP_ATR_MULT`/`TARGET_R` so the CLI matches the live bot; the lab
  defaults to 0 (off).
- **Metrics** (`compute_metrics`): also `profit_factor`, `expectancy` ($ per
  closed trade), `avg_win`, `avg_loss`, `avg_r`, `sharpe` (daily equity
  returns, intraday resampled to session close, √252).
- `livebot.py` gotchas (fixed 2026-10-27): Alpaca's `BarSet.df` columns are
  lowercase with a `(symbol, timestamp)` UTC index — `bars_from_alpaca()`
  title-cases them and converts to NY before `compute_signals`; positions are
  read with `get_all_positions()` so an API error aborts the cycle instead of
  reading as "flat"; bars end 16 min ago (free plan has no recent SIP data).
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
| `output/livebot_state.json` | `livebot.py` | `{open_trade: {entry_date, entry_price, qty, stop, target, order_id} \| null, entries: [{date, ts, symbol, max_loss, order_id}] (7 days), acted_signal_bar, last_block}` — survives restarts; feeds the daily-loss / trades-per-day / duplicate checks. An untracked Alpaca position is adopted at `avg_entry_price` |
| `logs/bot.log` | `livebot.py` | every signal/order/fill, risk BLOCK reasons (logged once per change) |
| `output/scan_results.json` | `scanner.py` / `POST /api/scanner/run` / MCP `scan_market` | `{ts, mode, scope, requested, with_data, seconds, filters, hits[{symbol, name, setups[], close, change_pct?, rsi?, volume, dollar_volume, at?, range_high?}]}` |
| `output/scanner_universe.json` | `scanner.py` | Nasdaq Trader common-stock universe, refreshed after 20h |
| `output/signal_config.json` | Signals tab (`POST /api/signals/config`) | `{lists: [{id, name, symbols, telegram_timeframes[], discord_timeframes[], patterns, analysis}]}` — per-channel timeframe sets; `analysis` is `{enabled, block, min_score, show_details, checks, smc}` (missing on old files loads as off / no block / min 3 / details on / every check off / every `smc` flag off; `block` is forced off when `enabled` is false; `min_score` is 1–5; `checks` is `{trend, volume, rsi, macd, support}` booleans — a check set true blocks that alert when it fails, independent of the score; n/a does not block; `show_details` false keeps only the `Confluence: n/m` line in the alert; `smc` is `{zones, structure, liquidity}` booleans — when `enabled`, any true flag adds that Smart Money layer to the alert PNG only, not confluence); legacy `timeframes` on a list (or flat file) applies to both channels on load; `discord_timeframes` may include `1w` (weekly bars, yfinance `1wk`; legacy `w` coerces to `1w`) and may list TFs whose webhook is not yet set (UI shows “no webhook”; bot retries until env is wired); Telegram timeframes do not include `1w`; each list is its own watchlist with its own scan schedule; legacy `poll_minutes` / `discord_route` on disk are stripped on save |
| `output/signal_log.json` | `signalbot.py` | every sent alert incl. dedupe key (`list_id\|SYMBOL\|tf\|bar_ts\|pattern`) — retried until required channel sends succeed; capped at 1000 entries. With analysis on, each row also has `confluence` `{score, total, checks[]}` and later `outcome` (`win` target first, `loss` stop first or both on the same bar, `expired` after 100 bars). Rows with `filtered: true` were blocked (score under `min_score`) and never delivered; they still dedupe and feed `/api/signals/stats` |
| `output/signal_heartbeat.json` | `signalbot.py` | `{ts}` of the last scheduler wake — `/api/signals/status` treats the bot as running when age &lt; `SIGNAL_HEARTBEAT_STALE_SECONDS` (120 s) |
| `output/discord_webhooks.json` | `discord_sync.py` / `POST /api/signals/discord-sync` | synced route → webhook URL + channel metadata; used when env has no URL for that route |
| `logs/signalbot.log` | `signalbot.py` | wake/idle, scan result per job, SIGNAL, dedupe/delivery warnings |

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
| `GET /api/signals/config` | — | `{config: {lists: [...]}, patterns: [...], telegram_configured, discord_configured: {stock_1m, …, crypto_news}, discord_available_timeframes: string[]}` — TF list = timeframes where stock or crypto webhook resolves |
| `POST /api/signals/config` | JSON object = same shape as `config` (symbols uppercased/deduped, list ids/names unique; legacy `timeframes` fills both channel arrays if channel fields omitted) | validates Telegram timeframes (`1m 5m 15m 30m 1h 1d`), Discord timeframes (`1m`–`1w`; may be empty), and pattern ids, writes `output/signal_config.json`, returns the saved config; `400` if body is not a JSON object or validation fails |
| `GET /api/signals/status` | — | `{running, last_check?, heartbeat_age_s?}` from the heartbeat file |
| `GET /api/signals/history` | `limit` (def 50), `include_filtered` (`1`/`true`/`yes`) | `{signals: [...]}` newest first — every alert ever sent (key, ts, list, symbol, timeframe, pattern_id, label, direction, close/entry/stop/target, bar_ts, optional `confluence`, `outcome`, `filtered`). Filtered (blocked) rows are omitted unless `include_filtered` is set |
| `GET /api/signals/stats` | — | `{by_score: [{score, total, wins, losses, expired, pending}], sent: {…}, filtered: {…}}` from the signal log. `pending` = no outcome yet |
| `GET /api/timemachine` | `symbol entry stop target` + optional `side` (long/short) `runs` (100–20000) `seed` (19) `at` (`YYYY-MM-DD HH:MM` NY) | `timemachine.replay()` dict; `400` for crypto, bad levels, < 20 full sessions |
| `GET /api/scanner` | — | `{results \| null, running, started, error}` |
| `POST /api/scanner/run` | JSON `{mode: daily\|orb, scope: watchlists\|universe}` | `202` and runs in a thread; `409` while one runs |
| `POST /api/signals/test` | — | sends a plain "test" message to Telegram so you can verify the keys; `{ok}` or `502` with the API error |
| `POST /api/signals/discord-test` | JSON `{route: stock_1m\|…\|crypto_news}` (legacy aliases `day`, `hour`, `minute`, `week` → stock routes) | test message to resolved webhook (env, then synced file); `{ok}` or `400`/`502` |
| `GET /api/signals/discord-sync` | — | last sync snapshot (no Discord API call): `{synced_at?, routes{route→channel_name}, unmatched_channels[], conflicts[]}` |
| `POST /api/signals/discord-sync` | JSON `{create_webhooks?: bool}` (default false) | scan guild channels, match names, write `output/discord_webhooks.json`; `{synced_at, routes[], unmatched_channels, conflicts, skipped_no_webhook?}` or `400` if bot token/guild missing |

Data windows per timeframe: `1d` → `BACKTEST_START` (2009); `1m` → 7 days;
`5m`/`15m`/`30m` → 60 days; `1h` → 730 days (yfinance caps). Intraday
requests with a `start` older than the cap are clamped to the window
(default no-range intraday run = full window, ~60 *trading* days). Daily
`end` is inclusive (backend adds one day for yfinance's exclusive end).
Tickers ending in `-USD` (`BTC` and `ETH` normalize to `BTC-USD` / `ETH-USD`)
load Bybit spot klines (`BTCUSDT`) instead of yfinance. Missing
`BYBIT_API_KEY` or `BYBIT_SECRET_KEY` returns `400`. The live SPY bot stays
on Alpaca paper and never sends Bybit orders.

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
  params, returns `{model, analysis}`), `useSignalsConfig` (fetched once; the
  form remounts via a config-derived key after save — no setState-in-effect),
  `useSignalsStatus/useSignalsHistory/useSignalsStats` (30 s polling),
  `useDiscordSyncStatus` (last guild sync snapshot, fetched on mount),
  `syncDiscordWebhooks(createWebhooks?)` and `sendDiscordSignalTest(route)` (POST
  mutations for Discord sync and per-route test messages).
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
- Ticker symbols are user input — a typo yields a clean 404, not a crash.
  `-USD` tickers that Bybit does not list also 404 when the kline list is empty.
- `.env` holds **paper** Alpaca keys only. Never add live Alpaca keys; the code
  hardcodes `paper=True`. Bybit keys are for spot **candles** only (`*-USD`);
  this repo does not place Bybit orders. Daily crypto candles are UTC, and the
  client drops a candle that has not closed yet (up to 40 pages × 1000 bars).

## 10. Extending

- **New strategy:** add a signal function + an entry in `STRATEGIES`
  (`strategies.py`); the API, `/api/strategies`, form fields and zod union
  (`web/src/lib/schemas.ts`) need the matching id/params/defaults.
- **ML advisor:** built-in — run a backtest, hit **Analyze with local LLM**
  in Trade results; `/api/analyze` (dashboard.py) re-runs the test and asks
  LM Studio (`http://127.0.0.1:1234/v1`, OpenAI-compatible) for a diagnosis
  and parameter suggestions bounded by the strategy's allowed ranges.
- **Risk controls:** live entries are gated by `risk.py` and protected by
  bracket legs; tests in `tests/test_risk.py` and `tests/test_livebot.py`
  (fake Alpaca client). The backtest shares the bracket rule; sizing in the
  backtest is still fixed `qty`.
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

## 13. Signals feature (candlestick pattern alerts) — last verified 2026-09-10

- **Patterns** (`candle_patterns.py`, all must complete on the *last closed* bar):
  bullish/bearish engulfing, morning/evening star, three white soldiers /
  black crows. Rules are strict (solid bars = body ≥ 50% of range; stars need a
  ≤ half-body doji-ish middle candle whose center is past the first bar's
  midpoint and a third bar closing beyond it). Unit-tested on synthetic OHLCV:
  12 positive/negative cases + multi-pattern detection, all passing.
- **Alert flow:** `signalbot.py` schedules `(list_id, symbol, timeframe)` jobs per
  watchlist (union of that list's `telegram_timeframes` and `discord_timeframes`);
  each job runs after the corresponding bar closes (NY boundaries). Fetches bars
  (in-progress bar dropped), detects patterns, computes entry/stop/target (R:R
  1:2), sends Telegram for TFs in `telegram_timeframes` and Discord for TFs in
  `discord_timeframes` using automatic route mapping (`1d`→day webhook, etc.) —
  text + matplotlib PNG (`chart_image.py`, includes R:R overlay). Dedupe key
  `list_id|SYMBOL|tf|bar_ts|pattern` is logged after every **selected** channel
  for that timeframe succeeds (Telegram and/or Discord); legacy dedupe keys
  without `list_id|` still suppress re-sends after upgrade. Failed or
  misconfigured channel sends are retried on the next scheduler wake (config is
  re-read each wake).
- **Confluence** (per watchlist `analysis`, off unless `enabled`): after a
  pattern hits, `confirmations.evaluate()` scores trend (close vs EMA 200),
  volume (≥ 1.5× prior 20 bars; no volume → n/a), RSI(14) pullback (LONG: < 40
  in the last 3 bars and not > 70 now; SHORT mirrored), MACD histogram
  direction, and support (pattern extreme vs EMA 50 or Bollinger 20, 2).
  n/a checks drop out of the denominator. The score is appended to the
  Telegram/Discord text (`show_details: false` sends only the score line).
  Each check in `checks` that is true is a hard filter: a fail blocks the
  alert even when the score is high; an n/a check does not. `block` +
  `min_score` (1–5, default 3) is a separate gate and logs the row with
  `filtered: true` when `score < min_score` (a 0/0 score is not blocked). Daily fetch windows are `2y` (yfinance) and 730 days
  (Bybit) so EMA 200 has history. `signal_outcomes.update_pending()` then
  walks bars already fetched this wake and sets `outcome` (see §6). The
  Signals tab shows the score (tooltip = checks), result, and a win-rate card.
  Checks, config validation, and outcome resolution are unit-tested (2026-10-03).
- **Crypto symbols:** on save/load, `BTC` and `ETH` normalize to `BTC-USD` and
  `ETH-USD`. Any ticker ending in `-USD` is fetched from Bybit spot
  (`BTC-USD` → `BTCUSDT`), not yfinance. Other tickers stay on yfinance.
  Bare `BTC`/`ETH` on Yahoo are Grayscale ETFs.
- **Verified end-to-end:** all five `/api/signals/*` endpoints (incl. 400 on bad
  timeframe), test message delivered to Telegram, and one real alert — TSLA 15m
  bearish engulfing on the last closed bar — sent with chart image and shown in
  the UI history table. `signalbot.py` runs its cycle loop (one log line per
  cycle) and re-reads config each cycle, so Signals-tab edits apply live.

## 14. Fundi analyst room (5-step protocol) — last verified 2026-10-24

`analystbot.py` runs its own process (`python analystbot.py`) and keeps a locked
`fundi-analyst` Discord channel: a pinned select menu (stock watchlists only) and
no free typing. The flow:

1. **Pick a ticker** → a `HoldingModal` popup asks for an optional position
   (shares held, average price, dollar risk — default `ANALYST_RISK_USD`; blanks
   = fresh cash entry). Validation (in `parse_holding`): shares + avg price must
   come together and be positive; risk-only sends the custom risk with `qty: null`.
2. **Gather** (`analyst_context.gather_symbol_data`, thread off the Discord
   loop): fundamentals snapshot + news + recent signals (the old payload) plus
   new step blocks — `price_action` (gap, volume ratio, absorption/distribution),
   `ex_dividend`, `macro` (`market_context.py`, TTL-cached, `MACRO_TTL_MIN`),
   `smc_daily`/`smc_15m` (`smc.snapshot_smc`, LuxAlgo settings), `squeeze_daily`/
   `squeeze_15m` (`squeeze.py`), `atr_daily`, `trade_plan` (`trade_plan.py`
   entry/stop/TP1/TP2/size from `ANALYST_RISK_USD` or the modal risk), and
   `charts` with `1d`/`15m` PNG bytes (`analysis_charts.py`, last 120 bars,
   SMC overlay + squeeze sub-panel). Every sub-step is failure-isolated; errors
   collect in `fetch_errors` / `snapshot_errors` and surface as
   `data_problem_notes` to the model.
3. **LLM** (`analyst_context.run_analysis` -> `analyst_steps.build_card`): **one
   small call per step** instead of one giant card (the single call made local
   models loop and mix up fields). Steps 1-4 each get only their own slice of the
   data (`step_slice`: bar indices stripped; stale/inconsistent SEC fundamentals
   dropped by `clean_fundamentals`, noted as a data gap), their own guidance and a
   6000-token first rung (`_analysis_call` still escalates 6000 -> 15000 -> 33000).
   Steps 3-4 also get the two chart PNGs (`ANALYST_VISION=1`, text-only fallback).
   **Step 5 is rendered in Python** (`render_execution`) from `trade_plan`, so its
   numbers are exact and "no TP2" is plain Hebrew. A fifth call picks the verdict
   from the four `סיכום:` lines + plan flags; code caps GO to WAIT when the plan has
   a quality flag (`tp1_rr_below_1`, `stop_far_atr`, `no_structure`, ...). Each LLM
   step is checked for numbers absent from its DATA and for leaked JSON key names,
   with one guarded retry; a dead step becomes a one-line gap, never kills the card.
   Chunks are posted with the chart files on the first message (Discord 2000-char
   chunks, per-user cooldown `ANALYST_COOLDOWN_S`).

**Fixed-ratio plan (1:3).** The popup has a 4th field "Invest $ for a 1:3 plan".
Filled -> `parse_holding` adds `invest_usd` -> `trade_plan.build_ratio_plan`: target
= nearest supply block (else top of the range), **stop = entry - (target - entry) / 3**
(`ANALYST_TARGET_RR`), shares = floor(invest / price), single 100% exit at the
target; no structural target above price -> 2 ATR stop and a target 3x that far
(`target_by_ratio`). Flags `stop_tight_atr` (< 0.5 ATR), `stop_far_atr`,
`invest_below_price` cap GO to WAIT. Step 5 text comes from `_render_ratio`.
Blank = the classic structural plan. **Private reply (`ANALYST_REPLY_MODE`, default `dm`):** the interaction is deferred
ephemeral, the briefing + charts go to the requester's DMs and an ephemeral
"sent to your DMs" note replaces the public post; `discord.Forbidden`/HTTP error
(DMs closed) -> posted in the channel with an ephemeral warning. `channel` = old public
behavior. **Menu repost:** whenever something landed in the channel (fallback, or
`channel` mode, also on failure) `AnalystBot.repost_menu` sends a fresh menu at the bottom and deletes the
previous one (no re-pin, to avoid "pinned a message" notices).

Gotcha: **thinking models loop in their reasoning** — Bonsai 27B is Q1_0 (1-bit)
and degenerated; Gemma-4 drafted the whole answer inside `reasoning_content` and
re-verified numbers until the budget ran out (empty `content`). LM Studio ignores
`chat_template_kwargs.enable_thinking=false` but honours **`reasoning_effort:
"none"`** (`ANALYST_REASONING_EFFORT`, default `none`; `llm_client.llm_chat_ex`
drops it on a server rejection). With it, gemma-4-26b-a4b-qat writes the whole
card in ~17 s (~536 words); set it empty to restore the server default. Current
model: `LLM_MODEL=google/gemma-4-26b-a4b-qat` (qwen/qwen3.8-27b is the slower,
more detailed alternative, ~200 s). Tests: `tests/test_analyst_steps.py`,
`tests/test_analyst_context.py`, `tests/test_analystbot.py`.

## 15. Ideas ported from day-trader-skill and mcp-trader — last verified 2026-10-27

- **Alert follow-ups:** `signal_outcomes.update_pending()` marks delivered
  rows that resolve to win/loss with `outcome_notified: false`;
  `signalbot.send_outcome_followups()` posts "🎯 Target hit $X (+2.0R)" /
  "🛑 Stop hit" on the same watchlist channels (list id from the dedupe key)
  and flips it true. Rows resolved before this feature have no flag and stay
  quiet; failed sends retry next wake.
- **NYSE calendar:** `market_calendar.py` is rule-based (Easter for Good
  Friday, weekend observance, no Saturday New Year's), unit-tested against
  nyse.com 2026–2027. `signalbot` skips stock jobs whose bar is outside a
  session (first run of a job always scans); crypto is never gated.
- **Time Machine on alerts:** intraday stock alerts (`TIMEMACHINE_ALERT_TFS`)
  fetch 60d of 5m bars once per wake (`bars_cache[(sym, "5m:60d")]`) and add a
  `Time Machine 2,000x/60d: TP …% · SL …% · open …% · avg …R → verdict` line;
  the log row keeps `timemachine{runs, sessions, p_target, p_stop, p_neither,
  avg_r, verdict}`. Pool = full 78-bar sessions only (no half days, no today);
  seeded (19) so results repeat.
- **Analyst room additions:** `relative_strength_vs_spy` in step 2,
  `volume_profile_60d` in step 3, quarter YoY (`revenue_yoy_pct`,
  `net_income_yoy_pct`, `eps_yoy_pct`) + margins in `last_quarters`, and
  8-K tags on news items. `ANALYST_ACCOUNT_USD` > 0 caps the step-5 size
  (25% position cap) and adds `size_capped_by_*` flags.
- **SEC 8-K news:** `news_client` polls `data.sec.gov/submissions` first;
  8-K/8-K/A of the last 30 days become items titled by item label, with
  `tags` (earnings 2.02, leadership 5.02, deal 1.01/2.01, restatement 4.02,
  bankruptcy 1.03, …). Discord pushes show `#tag`s.
- **Fixed:** SEC `revenue_ttm` / `net_income_ttm` / `eps_ttm` were the
  *average* of the last quarters (≈ one quarter), unlike every other
  provider's trailing sum. Now the sum of 4 *consecutive* quarters; when Q4
  only exists inside the 10-K (most companies) SEC leaves TTM to FMP /
  Finnhub / yfinance.
- **Fixed:** `/api/live` called `livebot.get_last_completed_bars` and hit the
  same lowercase-column `KeyError`, so the ticker tape never showed live
  Alpaca data; it now goes through `bars_from_alpaca()`.
- **mcp-trader:** its Tiingo key was hardcoded in `.cursor/*.json`,
  `docker_commands.bat`, `DOCKER_SETUP.md`, `TROUBLESHOOTING.md` and
  `.env.example`; it now lives only in that repo's `.env` (loaded by
  `load_dotenv`), and `.cursor/` is gitignored. Rotate the key if those files
  were ever shared.
- **Scanner ORB = today only:** yfinance `period="1d"` returns *yesterday's*
  session before the open, so `orb_breakout()` filters to `now.date()` and
  `run_scan("orb")` returns `{hits: [], note}` without fetching on a closed
  day or before 09:50 NY (range + one completed bar). The Scanner tab shows
  the note. Verified in the browser 2026-10-27 06:16 NY.
- **Dev servers:** `.claude/launch.json` defines `tradebot-api` (:8000) and
  `tradebot-web` (`next dev -p 3001`, since :3000 may be taken by another
  project). Restart the API after Python changes — Flask runs without reload.
- **signalbot survives a locked log file:** `signals._atomic_write` retries
  `os.replace` on `PermissionError` (10 × 0.2 s — Windows readers/Defender
  hold the file briefly; this crash stopped the bot on 2026-10-24). Each scan
  job and `signal_outcomes.update_pending` run inside `try/except` +
  `logger.exception`, so one failure never ends the loop.
- **analystbot log file is attached in `main()`** (`_log_to_file()`), not at
  import — the test suite used to write its fake "analysis failed" tracebacks
  into the real `logs/analystbot.log`.
- Session state (running PIDs, what was verified, open items):
  [HANDOFF_2026-10-27.md](HANDOFF_2026-10-27.md).
