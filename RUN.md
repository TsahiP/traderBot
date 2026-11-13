# Run TradeBot

## 1. Setup (one time only)
- Already done: `.env` has your Alpaca paper keys, `.venv` has all Python deps, `web/node_modules` is installed.

## 2. Start everything (Windows — recommended)
```powershell
.\start-all.ps1
```
Spawns **visible** consoles: `dashboard.py` (:8000), `npm run dev` (:3000), `paperbot.py` (:8100), `signalbot.py`, `fundabot.py`, `analystbot.py`; opens the browser and polls HTTP health on API / UI / paperbot. Lighter desk only (API + UI, optional paperbot): `.\start-web.ps1` or `.\start-web.ps1 -Paperbot`. Flags: `.\start-all.ps1 -NoAnalystbot -NoFundabot` etc. (`-NoBrowser`, `-NoHealthCheck`, `-Minimized`).

## 3. Start the dashboard (API, port 8000)
```bash
.venv/bin/python dashboard.py
```

## 4. Start the web UI (port 3000) — new terminal
```bash
cd web && npm run dev
```

## 5. Open it
- Go to http://localhost:3000 — backtest lab, stats, trade history and the **Signals** tab all work from here.

## 6. Optional: paper desk bot (new terminal)
- Trades your Alpaca **paper** account with the runs in `output/paperbot_config.json` (written with defaults on first start: `swing_sma` on SPY) and streams everything live to the **Paper desk** tab:
```bash
.venv/Scripts/python paperbot.py
```
- Or `.\start-web.ps1 -Paperbot` starts it with the API and the UI.
- HTTP/SSE on `127.0.0.1:8100`; journal in `output/paperbot.db`; logs in `logs/paperbot.log`. Stop with Ctrl+C (open brackets keep working at Alpaca; the next start reconciles).
- **"Ask before entering"** switch in the Paper desk: off = the bot enters by itself, on = every entry waits for Approve. Exits never ask.
- Discord, in its own room: create a `#paperbot` channel, add a webhook to it (Edit Channel → Integrations → Webhooks), then either put its URL in `.env` as `DISCORD_WEBHOOK_PAPER` or run **Sync from Discord**.
- The old single-symbol bot is still `livebot.py` (logs in `logs/bot.log`). The two refuse to run together - they share the paper account.

## 7. Optional: signal bot (new terminal)
- Watches the symbols/timeframes/patterns you pick in the **Signals** tab and pushes alerts (text + candlestick chart image) to Telegram when a pattern completes on the last closed bar:
```bash
.venv/bin/python signalbot.py
```
- Needs `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env` for Telegram timeframes. Watchlist symbols ending in `-USD` (including `BTC` / `ETH`, which save as `BTC-USD` / `ETH-USD`) need `BYBIT_API_KEY` and `BYBIT_SECRET_KEY` for Bybit spot candles; other symbols still use yfinance. Optional Discord (per watchlist `discord_timeframes` in the UI): per-timeframe stock/crypto webhooks in `.env` (see `.env.example`; legacy `DISCORD_WEBHOOK_DAY_TRADE`, `DISCORD_WEBHOOK_HOUR_TRADE`, `DISCORD_WEBHOOK_MINUTE_TRADE`, `DISCORD_WEBHOOK_WEEK_TRADE` still fall back for stock routes), or run **Sync from Discord** in the UI / `python -m integrations.discord_sync` with `DISCORD_BOT_TOKEN` + `DISCORD_GUILD_ID` (channel names like `stock-1h`, `crypto-5m` → `output/discord_webhooks.json`; env overrides synced URLs). The bot sleeps until the next bar close per job; config is re-read on each scheduler wake, so UI saves apply without a restart. Alerts retry until every selected channel for that timeframe delivers. Logs go to `logs/signalbot.log`.

## 8. Stop everything
- Ctrl+C in each terminal, or: `lsof -ti tcp:8000 -sTCP:LISTEN | xargs kill` and `lsof -ti tcp:3000 -sTCP:LISTEN | xargs kill` (plus `pkill -f signalbot.py` / `pkill -f livebot.py` / `pkill -f paperbot.py`)

## 9. Check logs if something breaks
- `logs/dashboard.out` — API errors
- `logs/bot.log` — livebot errors
- `logs/paperbot.log` — paperbot (every decision, order, fill and error)
- `logs/signalbot.log` — signal bot (one line per cycle + every SIGNAL)
- `logs/web.out` — web UI errors
