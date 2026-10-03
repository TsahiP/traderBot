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

Note: commission/slippage are not modelled; intraday fills may differ
slightly from the backtest.

## 2. Then paper trade

```powershell
python livebot.py
```

- Polls Alpaca's market clock, then reads the last **completed** daily bar
- On a buy signal with no open position -> market buy `QUANTITY` shares
- On a sell signal with an open position -> market sell
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
  checked on every fetch.
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

## 4. Signal bot (optional)

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

In each watchlist, pick **Discord timeframes** (multi-select; includes **`1w`**
for weekly candles — Telegram does not). Saved selections stay on disk even if a
webhook is temporarily missing; the UI labels them and the bot retries delivery
after you fix `.env`. Use the test buttons on the Discord card (grouped by
Stocks / Crypto / News). An alert is logged only after **all** channels selected
for that timeframe succeed.

More detail: `RUN.md` and `HANDSOFF.md`.

## Configuration (`config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `SYMBOL` | `SPY` | Ticker to trade |
| `SMA_FAST` / `SMA_SLOW` | `10` / `50` | Fast/slow SMA periods |
| `QUANTITY` | `10` | Shares per trade |
| `CAPITAL` | `100000` | Starting cash in the backtest |
| `POLL_INTERVAL_MIN` | `5` | Live-loop check interval (minutes) |

## Extending

- **Different strategy**: swap the logic inside `strategy.py` - both the
  backtest and the live bot pick it up automatically.
- **ML advisor**: `strategy.py` can call Qwen via LM Studio's local API
  (`http://127.0.0.1:1234/v1/chat/completions`) if you want model-assisted
  signals later.
- **Risk controls**: add stop-loss / position limits in `livebot.py` before
  ever considering real money.