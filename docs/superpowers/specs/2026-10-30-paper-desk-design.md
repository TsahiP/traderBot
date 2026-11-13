# Paper desk — live paper-trading bot + real-time UI

Date: 2026-10-30 · Branch: `more-dedicated-and-finance` · Status: phase 1 built (see §14 for what changed while building it)

## 1. Goal

Run the project as a live **paper** trader on Alpaca and watch it think in real
time: what it is waiting for, why it entered or skipped, what is open, where
the stop and target are, and how it did. The bot uses every analysis tool the
repo already has (strategies, scanner, candlestick patterns + confluence, SMC,
Time Machine, news/fundamentals, the local LLM) and decides by transparent
rules, so its results can be measured and compared over time.

### Decisions taken in brainstorming

| Topic | Decision |
|---|---|
| Who decides | Rules decide. The LLM is an optional veto with a three-way switch: `off` / `shadow` (runs, logs "would block", never blocks) / `veto` |
| Style | Day and swing runs side by side |
| Universe | Signals-tab watchlists + a pre-open scanner pass that adds up to N names per day. US stocks/ETFs via Alpaca only |
| Sides | Long **and short** in v1 |
| Process model | A separate `paperbot.py` process with its own HTTP server and **real push** (SSE) to the UI; SQLite journal underneath |
| UI controls | Pause/resume, Flatten all, close a single position, and an **"Ask before entering" switch** |
| Approval | Off by default: the bot enters on its own. Switch on in the UI → every new entry becomes a proposal that waits for Approve / Reject. Exits never ask |
| Adopted positions | Managed by the bot (rule in §5.8) |
| Notifications | Discord webhook posts in addition to the UI |
| LLM switch location | `output/paperbot_config.json` (not a UI control in v1) |

### Non-goals (v1)

- Real-money trading. `paper=True` is hard-coded, as in `livebot.py`.
- Crypto execution (Bybit stays market data only).
- Approving trades from Discord (webhooks are one-way; approval is UI only).
- Editing runs/risk limits from the UI (config file; the bot re-reads it). The
  one exception is the approval switch (§5.7), which the UI flips.
- Moving stops after entry (no trailing, no break-even move). A stop is never widened.

## 2. Architecture

```
            Alpaca paper
   ┌──────── TradingStream (order/fill updates, WebSocket) ─┐
   │         StockDataStream IEX (live trades/quotes)        │
   ▼                                                         │
┌────────────────── paperbot.py (one process) ──────────────┴──┐
│  Scheduler ─► Brain (runs + funnel) ─► Executor ─► Alpaca REST│
│      │              │                     │                   │
│      └──────► EventBus ◄──────────────────┘                   │
│                 │  ├─► SQLite output/paperbot.db (WAL)         │
│                 │  ├─► SSE subscribers (UI)                    │
│                 │  └─► Discord notifier (filtered)             │
│  HTTP 127.0.0.1:8100  GET /stream · GET /state · POST /commands│
└───────────────────────────────────────────────────────────────┘
        ▲ SSE + POST                       ▲ read-only (history, offline)
   web/ "Paper desk" tab ─────────────── dashboard.py :8000
```

- **Main thread**: the scheduler loop. It wakes at the next relevant moment
  (a bar close of any active timeframe, pre-open scan, flat-by, after-close
  swing pass, a command), never by a blind fixed sleep.
- **Stream threads**: alpaca-py `TradingStream` and `StockDataStream`
  (`feed=IEX`) each run their own asyncio loop in a daemon thread and push into
  a thread-safe queue the main thread drains. Reconnect with backoff; a
  disconnect is an `error` event and a UI badge, never a crash.
- **HTTP thread**: Flask (threaded) on `127.0.0.1:8100`. SSE via a generator
  per subscriber reading its own `queue.Queue`.
- **EventBus**: `publish(event)` = insert into `events` (gets the `id`), then
  fan out to SSE queues and the Discord notifier. Single writer: only the main
  thread writes the DB; stream/HTTP threads enqueue work for it.
- **Decisions only on closed bars.** Live quotes feed the display (P&L,
  distance to trigger) and MAE/MFE tracking, never an entry.
- `livebot.py` stays in the repo but must not run against the same account at
  the same time; its SPY SMA logic becomes the `swing_sma` run. `paperbot.py`
  refuses to start if `livebot.py` holds the lock file (`output/livebot.lock`,
  added to livebot) and vice versa.

### New / changed files

| File | Role |
|---|---|
| `paperbot.py` | Entry point: wiring (broker, journal, bus, streams, server, Discord), lock file, the tick loop |
| `paper/bot.py` | The loop: inbox (fills, quotes, commands), config reload, calendar jobs, heartbeat, `/state` snapshot |
| `paper/broker.py` | The only alpaca-py import: plain-dict adapter, always `paper=True` |
| `paper/reconcile.py` | Startup reconcile + adopted-position protection (§5.8) |
| `paper/lock.py` | PID lock files shared with livebot |
| `paper/config.py` | Load/validate `output/paperbot_config.json` (defaults written on first run), `config_hash` |
| `paper/journal.py` | SQLite schema, migrations, typed insert/query helpers (WAL, `busy_timeout`) |
| `paper/bus.py` | EventBus, SSE fan-out, replay from `Last-Event-ID` |
| `paper/server.py` | Flask app: `/stream`, `/state`, `/commands`, `/health` |
| `paper/runs.py` | Run definitions, per-(run, symbol) setup state machine |
| `paper/entry_timer.py` | Closed-bar trigger rules (ORB range, level, strategy registry signal) |
| `paper/exit_plan.py` | Stop/target rules (or-mid, or-far, atr:K, pct:X, structure), side-aware rounding, flat-by |
| `paper/funnel.py` | The decision funnel (§5.4): stages → `Decision` record |
| `paper/score.py` | Confluence score from `confirmations`, `smc`, `market_levels`, `market_context` |
| `paper/llm_gate.py` | Pre-emptive LLM verdicts, cache per setup, mode handling |
| `paper/executor.py` | Alpaca orders: bracket entries, flatten, cancel legs, adopted-position protection |
| `paper/streams.py` | TradingStream / StockDataStream threads → queue |
| `paper/notify.py` | Discord formatting + per-event-type filter |
| `risk.py` | Side-aware (long/short), short exposure cap, buying-power check for shorts |
| `livebot.py` | Writes/honours `output/livebot.lock` |
| `dashboard.py` | Read-only `/api/paper/*` from `paperbot.db`; Dashboard ledger reads paper trades too |
| `web/src/components/paper/*` | The Paper desk tab |
| `web/src/hooks/use-paper-stream.ts` | `EventSource` + SWR state, offline fallback |
| `web/src/lib/schemas/paper.ts` | zod schemas for every event/state payload |

## 3. Configuration — `output/paperbot_config.json`

Re-read on every scheduler wake (like `signal_config.json`); a change is a
`bot.config` event with the new `config_hash`. Invalid file → keep the last
good config and emit an `error` event. The file is plain JSON; the comments
below are documentation only.

```json
{
  "llm_mode": "shadow",                 // off | shadow | veto
  "llm_pending_policy": "proceed",      // at trigger with no verdict yet: proceed | skip
  "approval_mode": "auto",              // auto (default: just executes) | manual; flipped by the UI switch
  "approval_ttl_bars": 1,               // a proposal expires after one bar of its timeframe
  "approval_max_drift_pct": 0.3,        // expires early if price moved this far from the trigger
  "universe": {
    "watchlists": "all",                // or a list of watchlist ids from signal_config.json
    "scanner": { "enabled": true, "at": "09:00", "setups": ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold_bounce"], "max_add": 6 }
  },
  "global_risk": {
    "max_daily_loss_usd": 500, "max_trades_per_day": 6, "max_open_positions": 5,
    "max_open_risk_usd": 600, "max_short_exposure_pct": 50, "cash_buffer_pct": 10,
    "no_entries_before": "09:45", "no_entries_after": "15:30", "flat_by": "15:55"
  },
  "runs": [
    { "id": "day_orb", "enabled": true, "style": "day", "timeframe": "5m",
      "entry": { "kind": "orb", "or_minutes": 15, "min_volume_x": 1.0 },
      "exit": { "stop": "or-mid", "target_r": 2.0 },
      "allow_short": true, "min_score": 55, "timemachine": true,
      "risk": { "max_loss_per_trade_usd": 50, "max_daily_loss_usd": 150, "max_trades_per_day": 3, "max_position_pct": 25 } },
    { "id": "day_vwap", "enabled": false, "style": "day", "timeframe": "5m",
      "entry": { "kind": "strategy", "strategy": "vwap_reversion", "params": {} },
      "exit": { "stop": "atr:1.5", "target_r": 2.0 }, "allow_short": true, "min_score": 50, "timemachine": true,
      "risk": { "max_loss_per_trade_usd": 50, "max_daily_loss_usd": 150, "max_trades_per_day": 3, "max_position_pct": 25 } },
    { "id": "day_rsi", "enabled": false, "style": "day", "timeframe": "15m",
      "entry": { "kind": "strategy", "strategy": "rsi_mean_reversion", "params": {} },
      "exit": { "stop": "atr:1.5", "target_r": 2.0 }, "allow_short": true, "min_score": 50, "timemachine": false,
      "risk": { "max_loss_per_trade_usd": 50, "max_daily_loss_usd": 150, "max_trades_per_day": 3, "max_position_pct": 25 } },
    { "id": "swing_sma", "enabled": true, "style": "swing", "timeframe": "1d", "symbols": ["SPY"],
      "entry": { "kind": "strategy", "strategy": "sma_crossover", "params": { "fast": 10, "slow": 50 } },
      "exit": { "stop": "atr:2", "target_r": 2.0, "exit_on_opposite_signal": true },
      "allow_short": false, "min_score": 0, "timemachine": false, "risk": { "max_loss_per_trade_usd": 200 } },
    { "id": "swing_patterns", "enabled": true, "style": "swing", "timeframe": "1d",
      "entry": { "kind": "patterns", "patterns": "watchlist" },
      "exit": { "stop": "structure", "target_r": 2.0 },
      "allow_short": true, "min_score": 60, "timemachine": false, "risk": { "max_loss_per_trade_usd": 150 } }
  ],
  "discord": { "route": "paper", "events": ["proposal", "fill", "trade.closed", "error", "bot.offline", "day.summary"] }
}
```

A run without `symbols` trades the day's universe. Run risk limits are checked
in addition to the global ones (both must pass).

## 4. Daily timeline (New York time)

| Time | What happens |
|---|---|
| Startup (any time) | Lock file, load config, open DB, **reconcile** with Alpaca (§5.8), start streams, `bot.start` event |
| 09:00 | Universe build: watchlists + scanner `daily` pass (top `max_add` by dollar volume) → `scan.done`. Time Machine 60-day 5m pools preloaded for day-run symbols. News/fundamentals pre-filter. LLM pre-analysis queued for the list (swing verdicts from last night reused) |
| 09:30–09:45 | Day runs `ARMED`: opening range forming |
| Each bar close | Every enabled day run evaluates its symbols on the just-closed bar; setups move through the state machine; triggers go through the funnel |
| 15:30 | No new entries (`no_entries_after`; −3h on NYSE early closes via `market_calendar`) |
| 15:55 | `flat_by`: every day-run position (and today-opened adopted ones) → cancel legs + market exit |
| close + 20 min (16:20) | Swing pass on the completed daily bar (the free feed has no SIP data from the last 15 min, so the bar is final only then): signals → funnel → orders queued for the next open (submitted at `no_entries_before`, re-priced from the live quote; LLM has the night) |
| 16:10 | `day.summary` event (P&L, R, trades, blocked counts) → Discord |
| Weekends / holidays | `market_calendar` says closed: heartbeat only, streams idle |

## 5. The brain

### 5.1 Runs and the setup state machine

One state machine per (run, symbol, trading day), persisted in `setups`:

```
IDLE ──► ARMED ──► WAITING ──► TRIGGERED ──► (PROPOSED) ──► ENTERED ──► MANAGING ──► EXITED
  │        │          │            │              │
  └────────┴──────────┴──► EXPIRED / BLOCKED ◄────┘
```

- `ARMED`: the setup exists and is forming (ORB range building; VWAP deviation
  growing; SMA spread closing). Carries a human sentence: "range 9:30–9:45 forming".
- `WAITING`: the level is known; carries `level`, `side`, `distance_abs`,
  `distance_pct` and a sentence: "$0.42 under range high 164.10". Distance is
  updated live from quotes for display; the trigger still needs a closed bar.
- `TRIGGERED`: a closed bar met the rule → the funnel runs.
- `PROPOSED`: manual approval mode only.
- `EXPIRED`: past `no_entries_after`, the day ended, or a proposal timed out.
- `BLOCKED`: a funnel stage failed; reason kept. One trigger per setup per day
  (the Entry Timer rule): a later close does not re-trigger.

### 5.2 Trigger rules (`paper/entry_timer.py`)

- **Closed bars only.** A bar counts once the clock passes its start + its
  interval and the data has it; the in-progress bar is never used.
- **orb**: range = the bars inside the first `or_minutes`. Long trigger: first
  closed bar after the range that CLOSES above the range high (a close equal to
  the level is not above). Short: closes below the range low. Optional
  `min_volume_x` vs the range bars' average volume. Both sides armed until one fires.
- **strategy**: `strategies.run_strategy(id, bars, params)`; the last closed
  bar's `signal` (+1 long entry / −1 short entry, or exit for the open side).
- **patterns**: `candle_patterns` on the daily bar for the run's watchlist
  patterns; bullish → long, bearish → short (if `allow_short`).

### 5.3 Exit plan (`paper/exit_plan.py`)

| Rule | Long stop | Short stop |
|---|---|---|
| `or-mid` | OR midpoint | OR midpoint |
| `or-far` | OR low | OR high |
| `atr:K` | entry − K×ATR14 (run timeframe) | entry + K×ATR14 |
| `pct:X` | entry × (1 − X%) | entry × (1 + X%) |
| `structure` | `trade_plan` (below demand block / equilibrium) | mirrored: above supply block / equilibrium, shaded above the round number |

- Stop rounds **away** from the entry to the cent; target = entry ± R × risk,
  rounded half-up.
- Refused (stage fails, reason logged): stop on the wrong side, risk under
  0.05% of price, target already passed, day entry at/after `flat_by`.
- Day runs: TIF `day` legs + `flat_by`. Swing runs: TIF `gtc` legs, plus
  `exit_on_opposite_signal` where set.

### 5.4 The decision funnel (`paper/funnel.py`)

Every stage returns `{stage, status: pass|fail|skip|warn, value, reason, ms}`;
the full list is the `stages` JSON of the `decisions` row, and the UI renders
it as a checklist. First `fail` stops the funnel (remaining stages `skip`),
except `llm` in `shadow` mode, which never fails.

1. **universe** — symbol is in today's list for this run.
2. **position** — no open position or pending entry on this symbol in any run
   (Alpaca nets per symbol).
3. **news** — block: earnings inside the holding window (day: today; swing:
   next 5 sessions; shorts stricter: next 10), an important 8-K item (2.02,
   1.03, 3.01, 4.02, 5.02, 1.05) today, a trading halt. Warn: move ≥ 3% with no
   headline. Source: `news_client`, `funda_client` cached snapshots — no network
   call in the hot path.
4. **trigger** — the closed-bar rule fired (value: bar time, close, level).
5. **score** — confluence 0–100 for the trade's side (§5.5) ≥ `min_score`.
6. **exit_plan** — §5.3 produced a valid stop/target.
7. **timemachine** — (runs with `timemachine: true`, 5m day runs only) fail on
   "Strongly toward the stop"; value: target-first / stop-first / neither %,
   seed. Uses the preloaded pool; budget ~1 s (2,000 replays).
8. **llm** — §5.6.
9. **asset** — Alpaca asset `tradable`; shorts also `shortable` and
   `easy_to_borrow` (cached per day).
10. **size** — smallest of: max loss per trade / risk per share, position cap
    (`max_position_pct` × equity / price), max order, cash (longs:
    `(cash − buffer) / price`; shorts: `buying_power` with the same buffer).
    Value names the limiting factor. 0 shares → fail.
11. **risk** — `risk.check_entry` (side-aware) for the run limits, then the
    global limits (daily loss, trades/day, open positions, open risk, short
    exposure, window).
12. **approval** — `auto`: pass. `manual`: proposal (§5.7).
13. **execute** — bracket order submitted; `ENTERED` on the fill event.

### 5.5 Confluence score (`paper/score.py`)

Weighted, side-aware, each component listed with its points in the decision:

| Component | Source | Weight |
|---|---|---|
| Trend / momentum (EMA, RSI, MACD) | `confirmations.py` (direction = side) | 30 |
| Volume confirmation | `confirmations.py` volume check | 15 |
| SMC location (discount for longs / premium for shorts, nearest OB, recent BOS/CHoCH in the trade's direction) | `smc.snapshot_smc` | 25 |
| Relative strength vs SPY | `market_levels.relative_strength` (inverted for shorts) | 15 |
| Market context (QQQ trend, VIX regime) | `market_context` (cached by `MACRO_TTL_MIN`) | 15 |

A check that returns `None` (not enough data) is left out of the denominator,
as in `confirmations`. Weights live in the config file.

### 5.6 LLM gate (`paper/llm_gate.py`)

- The local model takes minutes, so it runs **ahead of the trigger**: when a
  setup enters `WAITING` (day) or when the 16:20 swing pass finds a signal
  (swing), a job is queued with the setup's data (bars, score components, SMC,
  news, planned stop/target) through the existing analyst pipeline
  (`analyst_steps`, `llm_client`). One worker thread, queue ordered by distance
  to trigger.
- Output: `{verdict: approve|block, confidence, reasons[], model, ms}`, cached
  per (run, symbol, day, side) and published as `llm.verdict`.
- At the funnel stage:
  - `off`: `skip`, nothing queued.
  - `shadow`: `pass` always; `llm_would_block` recorded.
  - `veto`: `block` → `fail`. No verdict yet → `llm_pending_policy`
    (`proceed` = pass with `llm_verdict: pending`; `skip` = fail).
- LM Studio down / timeout → verdict `error`; treated as pending.
- Every decision and trade stores `llm_mode`, `llm_verdict`,
  `llm_would_block`, so with/without comparisons are a query.

### 5.7 Manual approval (the "Ask before entering" switch)

- **Off (default, `approval_mode: auto`)**: the funnel's `approval` stage
  passes and the bot submits the order itself.
- **On (`manual`)**: every new entry stops at the `approval` stage as a
  proposal. Exits (stops, targets, flat-by, opposite signal, manual close,
  flatten) never ask.
- The switch is a UI control → `POST /commands {kind: "set_approval", args: {mode}}`.
  The bot writes the new value into `paperbot_config.json` (atomic replace
  with retry, as `signals._atomic_write`), so it survives a restart, and emits
  `bot.config`. The new mode applies to the next trigger.
- Turning it **off** while proposals are pending does not auto-execute them:
  they stay until approved, rejected or expired.
- Proposal = the full ticket (side, qty, ref price, stop, target, R, max loss,
  limiting size factor) + the funnel so far. Event `proposal.created`, Discord post.
- Expires after `approval_ttl_bars` bars of the run's timeframe, or early when
  the live price drifts more than `approval_max_drift_pct` from the trigger
  price (a late entry is a different trade) → `proposal.expired`.
- On approve: size and risk are **re-checked** at the current price before the
  order goes out (a stale approval cannot breach a limit).

### 5.8 Startup reconcile and adopted positions

- DB says open, Alpaca has no position → find the closing leg (stop/target
  fill) via the order history and record the trade; otherwise record at last
  price with `exit_reason: broker`.
- Pending entry orders in the DB are re-attached to their setups.
- Alpaca position not in the DB → **adopted** (`run_id: adopted`):
  - It already has open stop/target orders → adopt those levels as they are.
  - No protective orders → place an OCO: stop = avg entry ∓ 2×ATR14 (daily),
    target = 2R. If the price is already beyond that stop, place the stop
    1×ATR from the current price instead and flag `protective_stop_moved`.
  - Opened today (by fill time) → also flattened at `flat_by`; otherwise GTC.
  - Shown as `ADOPTED` in the UI; kept out of per-run statistics.

### 5.9 Trade management

- Fills, leg fills and cancels arrive from `TradingStream` → `orders` rows,
  `fill` / `trade.closed` events within a second.
- MAE / MFE tracked in R from live quotes while the trade is open.
- Exits: bracket leg (`stop` / `target`), `flat_by`, opposite signal (swing),
  `manual` (UI close), `flatten_all`, `broker` (closed outside the bot). Exits
  are never risk-gated.
- Partial fills: the trade's qty is the filled qty; the remaining entry is
  cancelled after one bar.
- Rejected order → setup `BLOCKED` with Alpaca's message; `error` event.

## 6. Journal — `output/paperbot.db` (SQLite, WAL, `busy_timeout=5000`)

```sql
events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, run_id TEXT, symbol TEXT, payload TEXT)
setups(run_id TEXT, symbol TEXT, day TEXT, state TEXT, side TEXT, level REAL,
       distance_pct REAL, sentence TEXT, detail TEXT, updated_at TEXT,
       PRIMARY KEY (run_id, symbol, day))
decisions(id INTEGER PRIMARY KEY, ts TEXT, run_id TEXT, symbol TEXT, side TEXT,
          outcome TEXT,            -- entered | blocked | vetoed | expired | rejected | proposed
          failed_stage TEXT, score REAL, stages TEXT, plan TEXT,
          llm_mode TEXT, llm_verdict TEXT, llm_would_block INTEGER, config_hash TEXT)
orders(alpaca_id TEXT PRIMARY KEY, parent_id TEXT, decision_id INTEGER, trade_id INTEGER,
       kind TEXT,                  -- entry | stop | target | flatten | manual | protective
       side TEXT, qty REAL, type TEXT, tif TEXT, status TEXT,
       limit_price REAL, stop_price REAL, filled_qty REAL, filled_avg REAL,
       submitted_at TEXT, updated_at TEXT)
trades(id INTEGER PRIMARY KEY, decision_id INTEGER, run_id TEXT, symbol TEXT, side TEXT, qty REAL,
       entry_ts TEXT, entry_price REAL, stop REAL, target REAL,
       exit_ts TEXT, exit_price REAL, exit_reason TEXT, pnl REAL, r REAL, mae_r REAL, mfe_r REAL,
       score REAL, llm_mode TEXT, llm_verdict TEXT, adopted INTEGER, config_hash TEXT)
commands(id INTEGER PRIMARY KEY, ts TEXT, source TEXT, kind TEXT, args TEXT, status TEXT, result TEXT)
equity(ts TEXT PRIMARY KEY, equity REAL, cash REAL, open_pnl REAL)   -- every minute in session
heartbeat(id INTEGER PRIMARY KEY CHECK (id = 1), ts TEXT, pid INTEGER, paused INTEGER,
          market TEXT, next_wake TEXT, streams TEXT, config_hash TEXT)
```

- Event types: `bot.start`, `bot.stop`, `bot.config`, `bot.paused`, `bot.resumed`,
  `scan.done`, `setup.state`, `trigger`, `decision`, `llm.verdict`,
  `proposal.created|approved|rejected|expired`, `order.submitted|updated|canceled|rejected`,
  `fill`, `trade.closed`, `position.adopted`, `command`, `quote` (not stored;
  SSE only, throttled to 1/s per symbol), `day.summary`, `error`.
- Retention: everything kept except `setup.state` events older than 30 days.
- `schema_version` in `PRAGMA user_version`; `journal.py` migrates forward.

## 7. HTTP API (paperbot, `127.0.0.1:8100`)

| Endpoint | Purpose |
|---|---|
| `GET /health` | `{ok, pid, uptime}` |
| `GET /state` | Full snapshot: heartbeat, account, positions (with plan, live price, P&L $/R), open orders, setups today, pending proposals, today's counters vs limits, config summary |
| `GET /stream` | SSE. `id:` = event id, `data:` = the event JSON, no `event:` field (one `onmessage`, `type` in the JSON). Honors `Last-Event-ID` or `?after=` (replays from SQLite, no duplicates), sends a `ping` comment every 15 s |
| `POST /commands` | `{kind, args}`; `kind` ∈ `pause`, `resume`, `flatten_all`, `close_position {symbol}`, `set_approval {mode: auto|manual}`, `approve {proposal_id}`, `reject {proposal_id}`. Returns `{command_id, status}`; the outcome also arrives as a `command` event |

- Commands are validated, written to `commands`, then run on the main thread.
  `flatten_all` and `close_position` work even while paused.
- CORS: only `http://localhost:3000` / `:3001`. Bound to 127.0.0.1 only.
- Next.js connects to `/stream` directly (rewrites can buffer SSE);
  `NEXT_PUBLIC_PAPERBOT_URL` defaults to `http://127.0.0.1:8100`.

`dashboard.py` adds read-only `GET /api/paper/state|trades|decisions|stats|equity`
straight from the DB, for when the bot is offline.

## 8. UI — "Paper desk" tab

Mockup reviewed in brainstorming. Main view, top to bottom:

1. **Status bar** — online/offline (heartbeat older than 10 s → offline),
   market state + NY clock, LLM mode, stream health; an **"Ask before
   entering" switch** (off = the bot just executes; on = proposals, §5.7);
   **Pause / Resume**; **Flatten all** (confirm dialog).
2. **Metrics** — equity, day P&L ($ and R), open risk vs daily budget, entries
   vs limit.
3. **Proposals** (only while the switch is on, or while proposals are still pending) — ticket, countdown, funnel as ✓/✗ chips
   (incl. "LLM would block"), Approve / Reject, expired cards greyed with reason.
4. **Open positions** — side/qty, run, live P&L $ and R, a stop→entry→target
   bar with the live price, flat-by or GTC tag, `ADOPTED` tag, **Close**.
5. **Waiting for** — `ARMED`/`WAITING` setups sorted by distance, with the
   sentence and the LLM verdict state.
6. **Live feed** — the event stream, colour-coded; click → detail.

Secondary views (sub-tabs):

- **Detail** (decision or trade) — candles via the existing `lab-chart` with
  entry/stop/target lines and markers, the full funnel with values, Time
  Machine odds, LLM reasons, orders and fills.
- **Journal & stats** — closed-trade table with filters (run, side, exit
  reason, LLM mode/verdict, adopted); win rate, expectancy, avg R, R histogram,
  MAE/MFE; **LLM comparison** (approved vs would-block, per run); intraday and
  cumulative equity curves.
- **Setups** — the whole day including `BLOCKED` / `EXPIRED` and why.

Tech: `usePaperStream()` keeps an `EventSource`, applies events onto the
`/state` snapshot held in SWR, and re-fetches `/state` after a reconnect. All
payloads are zod-validated (`lib/schemas/paper.ts`). Offline → falls back to
`/api/paper/*`, action buttons disabled with a reason. Toasts on command
results, fills and stops. Existing Dashboard ledger also lists paper trades
(tag `PAPER`).

## 9. Discord

- Its own room: route `paper` = env `DISCORD_WEBHOOK_PAPER`, or a webhook on a
  `#paperbot` channel (also `#paper-desk` / `#paper-bot`) found by the existing
  `discord_sync`; sent with `signals.dc_send`.
- Event filter from config. Default: `proposal`, `fill`, `trade.closed`,
  `error`, `bot.offline`, `day.summary`. Fills/exits include an optional chart
  image (`chart_image.render_candles` with the risk/reward overlay).
- Sent from a background queue with retry; a Discord failure never blocks trading.
- Proposals say "approve in the Paper desk" (no Discord buttons in v1).

## 10. Error handling

| Failure | Behaviour |
|---|---|
| Alpaca REST error in a cycle | Cycle aborted for that run, `error` event, retry next wake. Never treated as "no position" |
| Stream disconnect | Backoff reconnect; UI badge; on reconnect, open orders/positions re-synced via REST |
| Market data late (bar not there yet) | Wait up to 60 s for the bar; then skip that bar with a `warn` |
| LM Studio down | Verdict `error` → pending policy; banner in UI |
| Bot crash / restart | Lock file + startup reconcile (§5.8); open brackets live at Alpaca meanwhile |
| UI disconnected | The hook closes the stream and reconnects from a fresh `/state`, then `/stream?after=` just before it (the last 50 events refill the feed) - safe across a bot restart |
| Config invalid | Keep last good config, `error` event |
| DB locked (dashboard reading) | WAL + `busy_timeout`; dashboard opens read-only (`mode=ro`) |

## 11. Testing

- **Pure units** (pytest, no network): entry rules on fixture bars (ORB long
  and short, close-equals-level, volume filter), exit-plan rounding both sides,
  side-aware `risk.check_entry`, sizing limits, score with `None` checks,
  state-machine transitions, LLM gate per mode, approval switch (auto submits,
  manual proposes, switching off leaves pending proposals alone), proposal
  expiry (TTL + drift).
- **Funnel replay**: a recorded day of 5m bars (reuse the
  `day-trader-skill` TSLA 2026-10-19 fixture and the Time Machine fixture)
  driven by a fake clock and a **fake broker** (in-memory Alpaca double that
  fills brackets bar by bar) → asserts the full event sequence and trades.
- **Journal**: schema migration, `Last-Event-ID` replay, reconcile cases
  (closed by stop while offline, untracked position with/without legs,
  price beyond the planned protective stop).
- **Server**: Flask test client for `/state`, `/commands` validation, SSE
  framing.
- **Web**: zod schemas against recorded payloads; the hook's reducer
  (snapshot + events) as a unit test.
- **Manual**: one full paper session in the browser pane before calling it done.

## 12. Build order

Each phase ends usable and tested.

1. **Foundation** — `paper/` package, config, journal, bus, server + SSE,
   executor, streams, reconcile/adoption, lock file, `swing_sma` run ported,
   risk side-aware, **approval switch + proposals**. UI: status bar, metrics,
   proposals, positions, feed, Pause / Flatten / Close. Discord notifier.
2. **Day trading** — entry timer (ORB, strategy kind), exit plan, flat-by,
   universe + scanner pass, Time Machine stage, shorts end to end,
   "Waiting for" panel.
3. **Intelligence** — confluence score, news/fundamentals stage,
   `swing_patterns` run, LLM gate (off/shadow/veto) with pre-emptive queue.
4. **Analysis** — detail view with chart, Journal & stats, LLM comparison,
   equity curves, `day.summary`.

## 13. Open defaults (changeable in config, flagged here)

- Score weights and `min_score` per run are first guesses; tune after a few
  weeks of shadow data.
- `max_short_exposure_pct` 50, `approval_max_drift_pct` 0.3, scanner
  `max_add` 6.
- `day_vwap` and `day_rsi` ship disabled until `day_orb` has run a week.

## 14. Changes made while building phase 1

- The approval switch and proposals moved into phase 1 (asked for).
- Swing pass at close + 20 min instead of 16:05 (daily bar final on the free feed).
- `or-mid` / `or-far` stops exist already (pure arithmetic); `structure` waits for phase 3.
- Entries need a signal on the last completed bar (livebot also bought an old
  unacted cross); exits on an opposite signal stay lenient.
- The entry-window check uses the time the order will go out (`entry_at`),
  so a 16:20 decision for the 09:45 window passes the window.
- A queued entry that meets a paused bot, a passed window or a disabled run is
  blocked / expired with the reason, never sent late; Approve only inside the
  entry window with the market open.
- `ENTERED -> BLOCKED` is allowed for an entry order that dies unfilled at Alpaca.
- `/state` shows each run/symbol's latest setup (10-day window): a swing setup
  is dated by its decision day.
- The UI reconnects from scratch after a drop (see §10).
- Discord: `#paperbot` is the room; the UI's Sync does not create webhooks.
- Chart images on Discord fills/exits are not in phase 1 (text only).
