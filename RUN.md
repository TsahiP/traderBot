# Run TradeBot

## 1. Setup (one time only)
- Already done: `.env` has your Alpaca paper keys, `.venv` has all Python deps, `web/node_modules` is installed.

## 2. Start the dashboard (API, port 8000)
```bash
.venv/bin/python dashboard.py
```

## 3. Start the web UI (port 3000) — new terminal
```bash
cd web && npm run dev
```

## 4. Open it
- Go to http://localhost:3000 — backtest lab, stats, trade history and the **Signals** tab all work from here.

## 5. Optional: paper-trading bot (new terminal)
- Trades SPY on your Alpaca paper account on daily close, uses SMA crossover:
```bash
.venv/bin/python livebot.py
```
- Logs go to `logs/bot.log`. Stop it anytime with Ctrl+C.

## 6. Optional: signal bot (new terminal)
- Watches the symbols/timeframes/patterns you pick in the **Signals** tab and pushes alerts (text + candlestick chart image) to Telegram when a pattern completes on the last closed bar:
```bash
.venv/bin/python signalbot.py
```
- Needs `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env`. Config is re-read every cycle, so edits in the UI apply without a restart. Logs go to `logs/signalbot.log`.

## 7. Stop everything
- Ctrl+C in each terminal, or: `lsof -ti tcp:8000 -sTCP:LISTEN | xargs kill` and `lsof -ti tcp:3000 -sTCP:LISTEN | xargs kill` (plus `pkill -f signalbot.py` / `pkill -f livebot.py`)

## 8. Check logs if something breaks
- `logs/dashboard.out` — API errors
- `logs/bot.log` — paper bot errors
- `logs/signalbot.log` — signal bot (one line per cycle + every SIGNAL)
- `logs/web.out` — web UI errors
