# Paper desk — implementation plan, phase 1 (foundation)

Spec: [2026-10-30-paper-desk-design.md](../specs/2026-10-30-paper-desk-design.md).
Phases 2–4 get their own plan when phase 1 is merged (§ "Later phases" at the end).

**Phase 1 delivers:** `paperbot.py` running the `swing_sma` run (SPY, today's
livebot logic) on Alpaca paper, long and short capable, with the journal, live
SSE push, startup reconcile + adopted positions, the **"Ask before entering"
switch with proposals**, Discord notifications, and a Paper desk tab showing
status, metrics, proposals, open positions and the live feed, with Pause /
Resume / Flatten all / Close.

> Change from the spec's build order: the approval switch and proposals move
> from phase 2 into phase 1 (asked for explicitly). Spec §12 is updated in the
> same commit as this plan.

**Rules for every task:** test first (pytest, no network, fakes only), match
the repo's style (module docstring, small pure functions, `NY` zone, `config`
paths), run the full suite before moving on: `.venv\Scripts\python -m pytest -q`.
No task touches `livebot.py` behaviour except the lock file (T13).

---

## T1 — `risk.py` becomes side-aware

**Files:** `risk.py`, `tests/test_risk.py`

- `max_loss(qty, entry, stop, side="long")`: long `qty*(entry-stop)`, short `qty*(stop-entry)`.
- `size_position(..., side="long", buying_power=None)`: risk per share by side;
  shorts use `buying_power` (minus the buffer) instead of `cash` for the cash cap.
- `check_entry(..., side="long", buying_power=None)`: stop must be below (long)
  / above (short) the entry; reward:risk mirrored; the cash check uses
  `buying_power` for shorts.
- Default `side="long"` keeps every existing caller and test unchanged.

**Tests (new):** short pass; short stop at/below entry blocked; short R:R;
short `max_loss`; short sizing limited by buying power; all existing tests green.

## T2 — `paper/` package + config

**Files:** `paper/__init__.py`, `paper/config.py`, `tests/test_paper_config.py`, `config.py`

- `config.py`: `PAPERBOT_CONFIG_PATH`, `PAPERBOT_DB_PATH`, `PAPERBOT_LOCK_PATH`,
  `PAPERBOT_PORT = 8100`, `LIVEBOT_LOCK_PATH`.
- `default_config() -> dict` (spec §3, phase 1 runs: `swing_sma` enabled,
  others present but `enabled: false`).
- `load_config(path) -> (cfg, error | None)`: missing file → write defaults;
  invalid JSON / failed validation → return the last good config + error string.
- `validate_config(raw) -> dict`: enums (`llm_mode`, `approval_mode`,
  `llm_pending_policy`), numbers > 0, `HH:MM` times, unique run ids, known
  strategy ids (`strategies.STRATEGIES`), known stop rules.
- `config_hash(cfg) -> str`: sha1 of canonical JSON, first 10 chars.
- `set_approval(path, mode) -> dict`: read, set, validate, write with
  `signals._atomic_write`.

**Tests:** defaults validate; bad enum rejected; last-good fallback; hash stable
across key order; `set_approval` round-trip.

## T3 — Journal

**Files:** `paper/journal.py`, `tests/test_paper_journal.py`

- `Journal(path, readonly=False)`: `sqlite3` with `check_same_thread=False`,
  `PRAGMA journal_mode=WAL`, `busy_timeout=5000`; read-only opens
  `file:...?mode=ro` (for `dashboard.py`).
- Schema exactly as spec §6, `PRAGMA user_version = 1`, `migrate()` idempotent.
- Helpers: `add_event(type, payload, run_id=None, symbol=None) -> int`,
  `events_since(id, limit=500)`, `upsert_setup(...)`, `add_decision(...) -> id`,
  `upsert_order(...)`, `open_trade(...) -> id`, `close_trade(id, ...)`,
  `open_trades()`, `trades(filters)`, `add_command(...)`, `finish_command(...)`,
  `add_equity(...)`, `write_heartbeat(...)`, `read_heartbeat()`,
  `entries_today(day)` (rows with `symbol`, `max_loss` for `risk.check_entry`).
- `r` computed in `close_trade` from side, entry, stop, exit.

**Tests:** migrate twice is a no-op; events ids monotonic and `events_since`
pages; trade open→close computes pnl and R for long and short; read-only
connection cannot write; heartbeat single row.

## T4 — Event bus

**Files:** `paper/bus.py`, `tests/test_paper_bus.py`

- `EventBus(journal)`: `publish(type, payload, run_id=None, symbol=None, store=True) -> dict`
  inserts (unless `store=False`, used for `quote`), stamps `id`/`ts`, fans out.
- `subscribe() -> queue.Queue` (maxsize 1000; a full queue drops that subscriber
  and logs), `unsubscribe(q)`, `add_listener(fn)` for the Discord notifier.
- `replay(last_id)` → stored events after `last_id`.
- Thread-safe: subscriber list behind a lock; DB writes only through the bus
  on the main thread (other threads call `bus.post(fn)` to enqueue work —
  see T12).

**Tests:** publish stores and delivers to two subscribers; `store=False` not
in DB; replay after id; slow subscriber dropped without blocking others.

## T5 — Broker adapter + fake broker

**Files:** `paper/broker.py`, `tests/fakes/fake_broker.py`, `tests/test_paper_broker.py`

- `Broker` wraps alpaca-py `TradingClient(paper=True)` (paper is hard-coded)
  and returns plain dicts, so the rest of `paper/` never imports alpaca:
  `clock()`, `account()` (`equity`, `cash`, `buying_power`), `positions()`,
  `open_orders(symbol=None)`, `order(id, nested=True)`, `asset(symbol)`
  (`tradable`, `shortable`, `easy_to_borrow`),
  `submit_bracket(symbol, side, qty, stop, target, tif)`,
  `submit_oco(symbol, exit_side, qty, stop, target)`,
  `submit_market(symbol, side, qty, tif)`, `cancel(id)`, `latest_price(symbol)`,
  `daily_bars(symbol, days)` (reuses `livebot.bars_from_alpaca` logic).
- `FakeBroker` (tests): same interface, in-memory positions/orders, `fill(id, price)`,
  `trigger_leg(parent_id, "stop"|"target", price)`, configurable errors.
  Mirrors `tests/test_livebot.py`'s `FakeTrading` style.

**Tests:** fake contract tests (bracket creates parent + two legs; leg fill
closes the position; cancel removes; short position negative qty). The real
adapter gets one test with a stubbed `TradingClient` checking the request
objects (side, order class, TIF, stop/target prices).

## T6 — Exit plan (phase 1 subset)

**Files:** `paper/exit_plan.py`, `tests/test_paper_exit_plan.py`

- `plan(side, entry, rule, target_r, bars=None, or_high=None, or_low=None) -> Plan | Refusal`
  with rules `atr:K` and `pct:X` now (`or-mid`, `or-far`, `structure` in phase 2/3 —
  unknown rule → `Refusal`).
- Stop rounds away from entry to the cent (`math.floor` long / `math.ceil` short
  on cents), target half-up; refusals: wrong side, risk < 0.05% of price.
- `Plan` = `{side, entry, stop, target, risk_per_share, r_multiple, rule}`.

**Tests:** long/short ATR and pct; rounding direction both sides; too-tight refusal.

## T7 — Setup state machine + `swing_sma` run

**Files:** `paper/runs.py`, `tests/test_paper_runs.py`

- `SetupState` enum (spec §5.1) and `transition(setup, new_state, **detail)`
  that rejects illegal moves (table-driven).
- `Run` dataclass from config; `swing_sma` evaluation:
  `evaluate_daily(run, symbol, bars) -> list[Intent]` using
  `strategies.run_strategy("sma_crossover", ...)` on completed daily bars.
  `+1` → long entry intent; `-1` → exit intent for an open long (and short
  entry intent when `allow_short`). The "acted once per signal bar" rule from
  livebot carries over (stored in `setups.detail.acted_signal_bar`).
- Setup sentence for the UI: `"fast SMA 0.3% under slow"` etc.

**Tests:** ported from `test_livebot.py`: crossover → intent, same bar not
re-acted, warmup bars → no intent, sentences.

## T8 — Funnel (phase 1 stages)

**Files:** `paper/funnel.py`, `tests/test_paper_funnel.py`

- `run_funnel(intent, ctx) -> Decision` with stages, in spec order, for phase 1:
  `universe`, `position`, `trigger`, `exit_plan`, `asset`, `size`, `risk`
  (run limits then global limits: daily loss, trades/day, open positions, open
  risk, short exposure, window), `approval`. The `news`, `score`,
  `timemachine`, `llm` stages exist and return `skip` with reason
  `"phase 2/3"` so the UI shape is final.
- `ctx` carries account, open positions/orders, `entries_today`, config,
  clock, asset lookup — all plain data, so the funnel is pure.
- `Decision` = `{outcome, failed_stage, stages[], plan, qty, limited_by, llm_*}`.
- `approval`: `auto` → pass; `manual` → outcome `proposed`.

**Tests:** happy path entered; each stage's block reason; first fail skips the
rest; manual → proposed; short blocked when not `easy_to_borrow`; global
short-exposure cap.

## T9 — Executor + proposals

**Files:** `paper/executor.py`, `tests/test_paper_executor.py`

- `enter(decision)`: re-reads price, re-runs `size`+`risk` (cheap) and submits
  the bracket (`tif` from run style: swing `gtc`); writes `orders`, publishes
  `order.submitted`.
- `exit_position(symbol, reason)`: cancel open legs (wait like
  `livebot.cancel_symbol_orders`), market exit, publish.
- `flatten_all()`, `close_position(symbol)` → `exit_position(..., "manual")`.
- Proposals: `propose(decision)` stores `proposal.created` with expiry
  (`approval_ttl_bars` × run timeframe; for daily runs the expiry is the next
  entry window end); `approve(id)` → re-check then `enter`; `reject(id)`;
  `expire_due(now, prices)` (TTL and `approval_max_drift_pct`).
- Turning the switch off leaves pending proposals untouched (spec §5.7).

**Tests:** with `FakeBroker`: bracket request fields for long and short;
approve re-check blocks when the price ran (loss over limit); expiry by time
and by drift; reject; exit cancels legs first; flatten closes all incl. adopted.

## T10 — Reconcile + adoption

**Files:** `paper/reconcile.py`, `tests/test_paper_reconcile.py`

Spec §5.8, each case a test:
1. DB open trade, no position, stop leg filled → trade closed `stop` at the leg price.
2. Same, no leg info → closed `broker` at last price.
3. Pending entry in DB still open at Alpaca → re-attached.
4. Untracked position with stop+target orders → adopted with those levels, no new orders.
5. Untracked, no orders → OCO at avg ∓ 2×ATR / 2R.
6. Untracked, price already beyond that stop → stop 1×ATR from price, `protective_stop_moved`.
7. Untracked short position → mirrored levels.
8. Adopted and opened today → flagged `flat_by` (honoured by the scheduler).

## T11 — Streams

**Files:** `paper/streams.py`, `tests/test_paper_streams.py`

- `TradingStreamThread(key, secret, out_queue)` and
  `QuoteStreamThread(key, secret, out_queue, symbols)` (IEX): daemon threads,
  own asyncio loop, reconnect with backoff (1, 2, 5, 10, 30 s), push normalized
  dicts `{"kind": "trade_update"|"quote"|"stream_status", ...}`.
- `QuoteStreamThread.set_symbols(...)` thread-safe (subscribe/unsubscribe diff).
- Pure `normalize_trade_update(raw)` / `normalize_quote(raw)` are what the
  tests cover; the threads themselves are thin.

**Tests:** normalizers on recorded payload shapes (fill, partial_fill,
canceled, rejected; quote); symbol diff.

## T12 — `paperbot.py` main loop

**Files:** `paperbot.py`, `tests/test_paperbot.py`

- `main()`: lock file (`PAPERBOT_LOCK_PATH`, refuse if `LIVEBOT_LOCK_PATH`
  is held by a live pid), load config, journal, bus, broker, reconcile, start
  streams + HTTP server (T14), `bot.start`.
- `Bot.tick(now)` — one pass, fully testable with a fake clock:
  1. drain the inbox (stream events → orders/trades/MAE-MFE; commands from T14);
  2. reload config if the file changed → `bot.config`;
  3. due jobs: 16:05 swing pass (decisions; entries queued for the next
     window), `no_entries_before` submission of queued swing entries,
     `flat_by` for flagged positions, proposal expiry, minute equity snapshot;
  4. heartbeat row every tick.
- `Bot.next_wake(now)`: the earliest due job, capped at 1 s while the inbox is
  non-empty-capable (the loop blocks on `inbox.get(timeout=...)`, so commands
  and fills wake it immediately).
- Pause: blocks new entries/proposals only; exits and commands still run.
- Market closed/holiday via `market_calendar` + `broker.clock()`.

**Tests:** fake clock + `FakeBroker` day: 16:05 crossover → queued → 09:45
next day bracket → leg fill via a fake stream event → `trade.closed` with R;
pause blocks entry but not exit; manual mode → proposal → approve via command
→ order; config change event; heartbeat written.

## T13 — `livebot.py` lock file

**Files:** `livebot.py`, `tests/test_livebot.py`

- `main()` writes `LIVEBOT_LOCK_PATH` with its pid, refuses to start when
  `PAPERBOT_LOCK_PATH` holds a live pid; removes its lock on exit.
- Shared helper `paper/lock.py` (`acquire(path, other_path) -> bool`, pid
  liveness via `os.kill(pid, 0)` / `OpenProcess` on Windows).

**Tests:** stale lock (dead pid) is taken over; live other lock refuses.

## T14 — HTTP server + SSE

**Files:** `paper/server.py`, `tests/test_paper_server.py`

- Flask app factory `create_app(bot_facade)`; run with `threaded=True` on
  `127.0.0.1:PAPERBOT_PORT` in a daemon thread.
- `GET /health`, `GET /state` (snapshot built by the bot on the main thread
  and cached each tick — the HTTP thread never touches the broker),
  `GET /stream` (SSE: `id:`, `event:`, `data:`; `Last-Event-ID` replay; `: ping`
  every 15 s), `POST /commands` (validate `kind`/`args`, write `commands`,
  enqueue to the bot inbox, return `{command_id, status: "queued"}`).
- Commands: `pause`, `resume`, `flatten_all`, `close_position`,
  `set_approval`, `approve`, `reject`.
- CORS for `http://localhost:3000` and `http://localhost:3001` only.

**Tests:** Flask test client: `/state` shape; bad command 400; good command
queued + row written; SSE replay frames from a given id; CORS header.

## T15 — Discord notifier

**Files:** `paper/notify.py`, `tests/test_paper_notify.py`, `.env.example`

- Bus listener → background queue → `signals.dc_send` to
  `DISCORD_WEBHOOK_PAPER` (or the synced `paper` route); retry 3× with backoff;
  failures logged, never raised.
- `format_event(event) -> str | None` for `proposal.created`, `fill`,
  `trade.closed`, `error`, `bot.offline`, `day.summary`; others → `None`.
  Filter from `config.discord.events`.

**Tests:** formatter text per type (long/short, R sign); filter; send failure
does not raise.

## T16 — `dashboard.py` read-only paper API

**Files:** `dashboard.py`, `tests/test_dashboard_paper.py`

- `GET /api/paper/state` (heartbeat + open trades + today's counters from the
  DB, `online` = heartbeat < 10 s old), `/api/paper/trades`,
  `/api/paper/events?after=`, `/api/paper/equity`. Read-only `Journal`.
- Missing DB → `{online: false, empty: true}`, never 500.
- `/api/trades` also returns paper trades with `source: "paper"`.

**Tests:** with a temp DB fixture; missing DB; online flag by heartbeat age.

## T17 — Web: schemas, stream hook, reducer

**Files:** `web/src/lib/schemas/paper.ts`, `web/src/lib/schemas.ts`,
`web/src/hooks/use-paper-stream.ts`, `web/src/lib/paper-reducer.ts`,
`web/src/lib/schemas/core.ts` (`source` enum gains `"paper"`)

- zod schemas: `PaperState`, `PaperEvent` (discriminated union on `type`),
  `CommandResponse`.
- `paperReducer(state, event)`: pure; applies `fill`, `order.*`, `trade.closed`,
  `proposal.*`, `bot.config`, `quote` (live price → P&L $/R), `setup.state`.
- `usePaperStream()`: fetch `/state` from `NEXT_PUBLIC_PAPERBOT_URL`
  (default `http://127.0.0.1:8100`), open `EventSource(/stream)`, apply events,
  re-fetch `/state` on reconnect; when unreachable fall back to SWR on
  `/api/paper/state` with `online: false`. Exposes `sendCommand(kind, args)`.
- Follow `web/AGENTS.md` / `web/CLAUDE.md` conventions before writing code.

**Tests:** `web/` has no test runner (only `dev`/`build`/`lint`). Add
`tsx` as a dev dependency and a `"test": "tsx --test src/**/*.test.ts"`
script; `web/src/lib/paper-reducer.test.ts` runs the reducer on recorded
events with `node:test`. `npm run lint` and `npm run build` must pass too.

## T18 — Web: Paper desk tab

**Files:** `web/src/components/paper/paper-panel.tsx`, `status-bar.tsx`,
`metrics.tsx`, `proposals.tsx`, `positions.tsx`, `live-feed.tsx`,
`web/src/app/page.tsx`

- Tab "Paper desk" (icon `Activity`) after Dashboard.
- Status bar: online badge, market + NY clock, LLM mode label, stream health,
  **"Ask before entering" `Switch`** → `set_approval`, Pause/Resume, Flatten all
  (`AlertDialog`-style confirm; add the shadcn component if missing).
- Metrics: equity, day P&L $ and R, open risk / budget, entries / limit.
- Proposals: ticket, countdown, funnel chips, Approve / Reject.
- Positions: stop→entry→target bar with live price, P&L $/R, run, GTC /
  flat-by / `ADOPTED` tags, Close (confirm).
- Live feed: last 200 events, colour by type.
- Offline: buttons disabled with the reason as tooltip.
- Toasts (`sonner`) on command results, fills, stops.

**Verify:** browser pane on the running dev server (`tradebot-web`): offline
state with no bot; then with the bot running against paper — snapshot,
switch toggles and persists after a bot restart, Pause, a manual Close on a
1-share test position (placed by the bot in manual mode and approved in the UI).

## T19 — Run scripts and docs

**Files:** `.claude/launch.json` (add `tradebot-paperbot`), `start-web.ps1`
(optional `-Paperbot` switch), `RUN.md`, `README.md`, `HANDSOFF.md` §5,
spec §12 (phase 1 now includes the approval switch).

---

## Definition of done (phase 1)

- Full pytest suite green; new tests cover T1–T16.
- `paperbot.py` runs a whole session on the paper account: reconciles,
  streams, heartbeat, swing pass after the close, entries at the next window,
  bracket legs live, fills appear in the UI within ~1 s.
- The approval switch works both ways and survives a restart.
- Killing and restarting the bot loses nothing (events replayed, trades
  reconciled).
- `livebot.py` and `paperbot.py` refuse to run together.

## Later phases (own plans)

- **Phase 2 — day trading:** `entry_timer` (ORB, strategy kind on 5m/15m),
  `or-mid`/`or-far` stops, flat-by for day runs, universe builder + 09:00
  scanner pass, Time Machine stage with preloaded pools, `day_orb` / `day_vwap`
  / `day_rsi`, quote stream for watched setups, "Waiting for" panel.
- **Phase 3 — intelligence:** confluence score, news/fundamentals stage,
  `swing_patterns` + `structure` stops (long and short), LLM gate
  (off/shadow/veto) with the pre-emptive queue.
- **Phase 4 — analysis:** detail view with chart, Journal & stats, LLM
  comparison, equity curves, `day.summary`, `setup.state` retention.
