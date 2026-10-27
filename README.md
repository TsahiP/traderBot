# SMA Crossover Trade Bot (paper trading)

A Python starter bot that trades **SPY** with a simple SMA crossover strategy:
buy when the fast SMA crosses above the slow SMA, sell when it crosses below.
The exact same signal logic (`strategy.py`) powers both the backtest and the
live paper bot, so what you backtest is what you trade.

## Setup

1. **Create the venv and install:**

   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Get free Alpaca paper-trading keys:**
   - Sign up at https://app.alpaca.markets (free, no money needed)
   - Go to *Your Account > API Keys* (you may need to switch the view to
     "Paper" keys - they end with nothing special, the UI labels them)
   - Copy `.env.example` to `.env` and paste the keys in:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   The bot always talks to the **paper** account - it can never trade real
   money.

   **Crypto bars** (`BTC-USD`, `ETH-USD`, or `BTC` / `ETH` which become those
   tickers): add Bybit keys to the same `.env`. A ticker that ends in `-USD`
   loads spot candles from Bybit (`BTC-USD` → `BTCUSDT`). Stock tickers stay
   on yfinance, and the live bot stays on Alpaca paper. These keys are for
   market data only — the app does not send Bybit orders.

   ```text
   BYBIT_API_KEY=
   BYBIT_SECRET_KEY=
   ```

## 1. Backtest first (no keys needed)

```powershell
python backtest.py
```

Downloads ~15 years of SPY history via yfinance, simulates the strategy
(orders execute at the next bar's open), and prints total return, CAGR,
max drawdown, trade count and win rate versus buy-and-hold. Set `SYMBOL` in
`config.py` to a `-USD` ticker (for example `BTC-USD`) to download Bybit
spot daily bars instead — that path needs the Bybit keys above. Details are
written to `output/trades.csv` and `output/equity_curve.csv`.

The CLI applies the same bracket exits as the live bot (`STOP_ATR_MULT`,
`TARGET_R`; a bar that touches both counts as the stop) and also prints
profit factor, expectancy, average R and Sharpe.

Note: commission/slippage are not modelled; intraday fills may differ
slightly from the backtest.

## 2. Then paper trade

```powershell
python livebot.py
```

- Polls Alpaca's market clock, then reads the last **completed** daily bar
- On a buy signal with no open position -> a **bracket order**: market buy
  `QUANTITY` shares plus a GTC stop-loss (`STOP_ATR_MULT` × daily ATR below
  the entry) and a GTC take-profit (`TARGET_R` × the risk above it), both
  held at Alpaca so they work even while the bot is off
- Every entry must pass the **risk gate** (`risk.py`) first: a stop, max loss
  per trade, minimum reward:risk, daily loss budget, trades per day, one
  entry per symbol per day, the NY entry window, a position cap and a cash
  buffer. A blocked entry is logged with every reason; exits are never blocked
- Each crossover is acted on once — after a stop-out the bot waits for the
  next crossover instead of buying the same signal again
- On a sell signal with an open position -> cancel the bracket legs, then
  market sell. A stop or target filled at the broker is recorded on the next
  cycle
- State (open trade, today's entries) lives in `output/livebot_state.json`, so
  a restart keeps the entry price; a position the bot does not know is adopted
  at Alpaca's average entry price
- Logs every action to `logs/bot.log` (rotates at 1 MB)
- Records every closed round trip to `output/live_trades.csv`

Stop with `Ctrl+C`.

## 3. Dashboard (Next.js UI)

```powershell
.\start-web.ps1        # starts API + frontend, opens http://localhost:3000
```

Or run the two servers yourself:

```powershell
python dashboard.py    # API on http://127.0.0.1:8000
cd web
npm install            # first time only
npm run dev            # UI on http://localhost:3000
```

The frontend is a Next.js 15+ app (shadcn/ui, SWR, zod) in `web/` — it proxies
`/api/*` to the Flask API, so the Python engine stays the single source of
truth. Main areas:

- **Dashboard** — sticky ticker tape (last close, SMAs, live signal), stat
  cards (account equity, position, realized P&L, win rate), equity curve
  chart, and the trade ledger. Auto-refreshes every 30s.
- **Backtest lab** — run a backtest on **any ticker** (default SPY) with any
  SMA pair, quantity and capital. Renders candlesticks with SMA overlays,
  ▲ buy / ▼ sell markers where the logic fires, volume bars, metrics and the
  full trade list. Inputs are validated with zod; API responses are schema
  checked on every fetch. Optional **Stop (× ATR)** / **Target (R)** add the
  live bot's bracket exits (STOP / TP markers); metrics include profit
  factor, expectancy per trade, average R and Sharpe.
- **Signals** — configure candlestick-pattern watchlists (symbols, patterns,
  separate **Telegram** and **Discord** timeframe sets per list). The signal bot
  scans each list on its own bar-close schedule and pushes alerts with a chart
  image when a pattern completes on the last closed bar. **Discord** timeframes
  route by symbol (stocks vs `-USD` crypto) and timeframe (`1m` … `1w`, plus
  news channels); set webhooks in `.env` or **Sync from Discord**. The UI only
  offers Discord TFs where a webhook resolves. Legacy configs with a single
  `timeframes` array apply to both channels. Test buttons per route on the
  Discord card.

Works with or without Alpaca keys — without them the tape falls back to the
latest backtest bar. Run `livebot.py` alongside and watch paper trades appear
as the bot opens and closes them.

## 4. Fundamentals + news bot (optional)

`python fundabot.py`

Watches every symbol in **all** of your Signals watchlists and keeps two data sets
fresh (sources are free tiers, ranked best -> worst, with per-provider daily
quota tracking in `output/funda_state.json`):

- **Fundamentals** (stocks): one snapshot per NY trading day after the close
  (plus a boot catch-up for stale symbols). The chain is
  **SEC EDGAR** (keyless, authoritative XBRL statements) → **FMP** (250/day:
  profile, key metrics, quarterly statements) → **Finnhub** (60/min: metrics,
  next-earnings date) → **yfinance** → **Twelve Data** (price) →
  **Alpha Vantage** (25/day; only its still-free `GLOBAL_QUOTE` + `EARNINGS`
  live endpoints). Every field remembers which provider supplied it. Crypto
  symbols (`-USD`) get a market snapshot (price, mcap, rank, changes) from
  keyless CoinGecko. Snapshots live in `output/funda/<symbol>.json`.
- **Fundi analyst digest**: once per NY day (same after-close window, 16:10 ET)
  every watchlist symbol gets **one Discord message** with its full
  fundamentals snapshot (price, valuation, margins, balance sheet, 52w, next
  earnings) **plus its latest news** — stocks to `stock_news`, crypto to
  `crypto_news`.
- **News**: polled every 4h and **pushed to your `stock_news` /
  `crypto_news` Discord channels** — **Finnhub** → **FMP** → **Alpaca** (your
  existing keys) → **Google News RSS** (keyless). Crypto news comes from
  CoinDesk / Cointelegraph / Decrypt RSS matched per coin. Items dedupe by URL
  across providers (`output/news_sent.json`).

Fun fact: with **zero API keys** the feature still works — SEC EDGAR +
yfinance + Google News + CoinGecko cover the essentials. Add keys to `.env`
for the rest of the chain:

```text
FMP_API_KEY=            # financialmodelingprep.com — 250/day, get this first
FINNHUB_API_KEY=        # finnhub.io — 60/min, news + metrics
ALPHAVANTAGE_API_KEY=   # alphavantage.co — 25/day (either underscore spelling works)
TWELVEDATA_API_KEY=     # twelvedata.com — 800/day, price fallback
```

The **Fundi** tab of the dashboard shows the snapshots (with per-field source
badges and quota usage) and the merged news feed; the refresh buttons work
even while `fundabot.py` is off. Logs: `logs/fundabot.log`.

### Fundi analyst room (Discord + local LLM)

```powershell
python analystbot.py
```

A Discord room where you don't type: the bot creates (and locks) a
`fundi-analyst` channel in your **tp-tbt** guild and pins a ticker select menu
built from your Signals watchlists (stocks only, max 25 per row). Pick a
ticker and a small popup asks for an optional position (shares held +
average price + dollar risk — blanks mean a fresh cash entry). The bot then
runs the 5-step protocol through your **local LM Studio** model and posts
the briefing with chart images:

1. **Catalysts** — fundamentals snapshot + merged news feed for the symbol.
2. **Macro** — QQQ regime, VIX, oil, your local name (ESLT), geopolitics.
3. **Smart Money** — SMC reads on 1D and 15M (range zone, OB demand/supply,
   BOS/CHoCH, equal highs/lows, open FVGs).
4. **Squeeze** — LazyBear Squeeze Momentum color + squeeze state on both
   timeframes.
5. **Execution** — ATR, absorption/distribution read, the trade plan
   (entry break, stop, TP1/TP2, position size from your dollar risk) and,
   if you filled in the popup, your live position P&L.

Every number is computed in code; the LLM only phrases the briefing from a
structured JSON block plus two rendered chart images (`1D` and `15M`, SMC
overlays + squeeze sub-panels). It must quote numbers verbatim or flag a
data problem; it ends with a `GO | WAIT | AVOID` verdict + confidence. The
briefing is written in **Hebrew** (tickers, numbers and technical terms stay
in English); the verdict line itself stays `GO | WAIT | AVOID - confidence X/5`.

Requirements:

- **LM Studio** listening at `LLM_BASE_URL` (default `http://127.0.0.1:1234/v1`)
  with the model from `LLM_MODEL` loaded (`analystbot` strips reasoning tags
  if the model thinks out loud).
- A bot invite with **View Channels, Send Messages, Manage Channels, Manage
  Roles** (needed to lock the room by denying @everyone Send Messages) and
  **Manage Messages** (pin the menu). The token comes from
  `DISCORD_BOT_TOKEN` or falls back to `DISCORD_TOKEN`.
- Optional env: `DISCORD_GUILD_ID` is auto-detected from the first guild the
  bot is in; channel name override via `DISCORD_ANALYST_CHANNEL`. Also
  `ANALYST_RISK_USD` (default dollar risk for the step-5 plan, 250),
  `ANALYST_VISION` (chart images to the model, `1`) and `MACRO_TTL_MIN`
  (macro-context cache lifetime, 10).

Behavior details: a 20 s per-user cooldown (`ANALYST_COOLDOWN_S`) stops
spam, only one analysis per symbol runs at a time, and replies are chunked
to fit Discord's 2000-char message limit; the chart PNGs are attached to the
first message (`ANALYST_VISION=0` keeps them off, and the model falls back
to text-only automatically if the server rejects images). If the model is
down, the room posts the error instead of staying silent. The menu re-syncs
from the watchlist every `ANALYST_MENU_REFRESH_MIN` minutes (default 10) —
save a watchlist in the UI and the picker follows. Logs: `logs/analystbot.log`.

## 5. Signal bot (optional)

Watches your watchlists from the **Signals** tab and sends pattern alerts
(text + PNG chart). Config lives in `output/signal_config.json` and is re-read
on each scheduler wake — save in the UI and the bot picks it up without a restart.

```powershell
python signalbot.py
```

Logs: `logs/signalbot.log`. Stop with `Ctrl+C`.

**Telegram** (for `telegram_timeframes` alerts): set in `.env`:

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Target chat or channel id (comma/space for multiple) |

Use **Send test** on the Telegram card in the UI to verify.

**Discord** (optional, per watchlist `discord_timeframes` — same message and
chart as Telegram when both are selected for that bar). Webhooks route by **symbol**
(stocks vs `-USD` crypto) and **timeframe** (separate channels for `1m`, `5m`,
`15m`, `30m`, `1h`, `1d`, `1w`). Legacy `DISCORD_WEBHOOK_*_TRADE` URLs still
work as fallbacks for the matching stock timeframe. See `.env.example` for
`DISCORD_WEBHOOK_STOCK_*`, `DISCORD_WEBHOOK_CRYPTO_*`, and news webhooks. Or use
**Sync from Discord** on the Signals tab (`DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`;
channels named `stock-1h`, `crypto-5m`, etc.) — env webhooks still override the synced file.

**Technical analysis** (per watchlist, off by default) appends a confluence score to the alert: trend vs EMA 200, volume ≥ 1.5× the prior 20 bars, an RSI pullback, MACD histogram direction, and a touch of EMA 50 or a Bollinger band. Checks without enough history are left out of the score (`3/4`). Each check has its own switch: when it is on, a fail blocks the alert on its own, separate from the score. **Show details** hides the per-check lines in the alert and the explanations in the form. **Block weak signals** still drops anything under the minimum score (default 3). Blocked rows stay in the log so the Confluence results card can compare sent vs blocked win rate (target before stop). The recent-signals table has its own **Details** switch for the score tooltip. Daily history pulled for the check is two years (stocks) or 730 days (Bybit). When analysis is on, optional **alert chart overlays** (checkboxes under minimum score) draw Smart Money layers on the Telegram/Discord candle image only: order blocks & fair value gaps, BOS/CHoCH labels, and liquidity sweeps — they do not change the score or blocking.

In each watchlist, pick **Discord timeframes** (multi-select; includes **`1w`**
for weekly candles — Telegram does not). Saved selections stay on disk even if a
webhook is temporarily missing; the UI labels them and the bot retries delivery
after you fix `.env`. Use the test buttons on the Discord card (grouped by
Stocks / Crypto / News). An alert is logged only after **all** channels selected
for that timeframe succeed.

More detail: `RUN.md` and `HANDSOFF.md`.

## 6. Scanner, Time Machine and MCP server

**Market scanner** (`scanner.py`, **Scanner** tab) finds setups across your
watchlists or every US-listed common stock (Nasdaq Trader symbol directories,
~5,500 names, a few minutes):

```powershell
python scanner.py daily --scope universe   # 20d breakout, bull flag, MA20 reclaim, oversold, bounce
python scanner.py orb                      # opening-range breakouts on today's 5m bars
```

Results: `output/scan_results.json`, most traded first.

**Time Machine** (`timemachine.py`) replays the rest of the session 10,000
times from the stock's own 5-minute bars (60 days) and reports the odds of
target first / stop first / neither, avg R, what really happened on past
sessions, and a verdict against the break-even rate `1 / (1 + R)`. Intraday
stock alerts get a one-line summary (`TIMEMACHINE_ALERT_RUNS`, 0 = off); the
API is `GET /api/timemachine?symbol=TSLA&entry=..&stop=..&target=..`.

**Alert follow-ups**: when a delivered alert hits its target or stop, the
signal bot posts "🎯 Target hit" / "🛑 Stop hit" to the same channels.

**MCP server** (`tradebot_mcp.py`) exposes the engine to Claude — read-only,
it never places orders. Tools: `analyze_symbol`, `run_backtest`,
`time_machine`, `scan_market`, `last_scan`, `fundamentals`, `news`,
`signal_history`, `signal_stats`, `risk_check`, `levels` (RS vs SPY + volume
profile), `market_status`.

```bash
claude mcp add tradebot -- D:\Tp\tradebot\.venv\Scripts\python.exe D:\Tp\tradebot\tradebot_mcp.py
```

Claude Desktop (`claude_desktop_config.json`):

```json
{"mcpServers": {"tradebot": {"command": "D:\\Tp\\tradebot\\.venv\\Scripts\\python.exe",
  "args": ["D:\\Tp\\tradebot\\tradebot_mcp.py"]}}}
```

## Configuration (`config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `SYMBOL` | `SPY` | Ticker to trade |
| `SMA_FAST` / `SMA_SLOW` | `10` / `50` | Fast/slow SMA periods |
| `QUANTITY` | `10` | Shares per trade |
| `CAPITAL` | `100000` | Starting cash in the backtest |
| `POLL_INTERVAL_MIN` | `5` | Live-loop check interval (minutes) |
| `STOP_ATR_MULT` / `TARGET_R` | `2.0` / `2.0` | Bracket stop = entry − 2 × ATR(14); target = entry + 2R |
| `RISK_MAX_LOSS_PER_TRADE_USD` | `200` | Loss at the stop allowed per entry |
| `RISK_MAX_DAILY_LOSS_USD` | `500` | Sum of the max losses of today's entries |
| `RISK_MAX_TRADES_PER_DAY` | `3` | Entries per NY day |
| `RISK_MIN_REWARD_RISK` | `1.5` | Minimum (target − entry) / (entry − stop) |
| `RISK_MAX_POSITION_PCT` / `RISK_CASH_BUFFER_PCT` | `25` / `10` | Position cap and free cash, % of equity |
| `RISK_NO_ENTRIES_BEFORE` / `_AFTER` | `09:45` / `15:30` | NY entry window (`None` turns it off); ends 3h earlier on a 1:00 PM early close |
| `RISK_SIZE_BY_RISK` | `True` | Size each entry to the tightest of max loss / position cap / cash buffer, capped at `QUANTITY` |

Any `RISK_*` limit set to `None` is switched off.

## Extending

- **Different strategy**: swap the logic inside `strategy.py` - both the
  backtest and the live bot pick it up automatically.
- **ML advisor**: `strategy.py` can call Qwen via LM Studio's local API
  (`http://127.0.0.1:1234/v1/chat/completions`) if you want model-assisted
  signals later.
- **Risk controls**: the live bot already uses bracket stops and the
  `risk.py` gate; tighten the `RISK_*` limits in `config.py`, and keep the
  backtest (`engine.py`) in step if you change the exit rule.