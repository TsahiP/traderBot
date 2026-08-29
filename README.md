# TradeBot — Multi-Strategy Paper Trading & Backtesting Platform

A full-stack algorithmic trading workstation with automated backtesting, an Alpaca paper-trading execution bot, real-time Telegram alerts, and an interactive Next.js analytics desk with TradingView charting and local LLM diagnostics.

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────┐
│               Next.js Web UI (:3000)                   │
│   • Dashboard & Live Ticker Tape                       │
│   • Strategy Lab with Lightweight Charts v5            │
│   • Local LLM AI Diagnostics                           │
└───────────────────────────┬────────────────────────────┘
                            │ /api/* proxy
                            ▼
┌────────────────────────────────────────────────────────┐
│                Flask API Server (:8000)                │
│                 (dashboard.py)                         │
└─────────────┬────────────────────────────┬─────────────┘
              │                            │
              ▼                            ▼
┌───────────────────────────┐┌───────────────────────────┐
│   Backtest Engine         ││   Paper Trading Bot       │
│   (engine.py,             ││   (livebot.py,            │
│    strategies.py)         ││    alpaca_service.py)     │
│ • Linear cost accounting  ││ • Alpaca paper execution  │
│ • Long & short simulation ││ • Telegram trade alerts   │
│ • EOD session flattening  ││ • Automatic position sync │
└───────────────────────────┘└───────────────────────────┘
```

---

## Prerequisites

- **Python**: 3.11 or 3.12
- **Node.js**: v18+ (v20+ recommended) and `npm`
- **Alpaca Account** (optional for backtests, required for live paper trading): Free paper API keys at [Alpaca Markets](https://app.alpaca.markets)
- **Telegram Bot** (optional): Token and Chat ID from [@BotFather](https://t.me/BotFather) for push alerts
- **LM Studio** (optional): Local OpenAI-compatible server at `http://127.0.0.1:1234/v1` for strategy lab AI critiques

---

## Quick Start

### 1. Clone & Environment Setup

```powershell
# 1. Create and activate Python virtual environment
python -m venv .venv
.\.venv\Scripts\activate

# 2. Install Python dependencies (includes pytest)
pip install -r requirements.txt

# 3. Install Frontend dependencies
cd web
npm install
cd ..
```

### 2. Configure Environment Variables

Create `.env` from the provided example:

```powershell
Copy-Item .env.example .env
```

Edit `.env` with your preferred settings:

```ini
# Alpaca Paper Trading Keys (free at https://app.alpaca.markets)
ALPACA_API_KEY=your_paper_key_here
ALPACA_SECRET_KEY=your_paper_secret_here

# Optional: Telegram Alerts
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# Optional: Local LLM Advisor (LM Studio)
LLM_BASE_URL=http://127.0.0.1:1234/v1
LLM_MODEL=auto
```

---

## Running the Platform

### Option A: One-Click Startup (Recommended)

Starts the Flask API, the Next.js frontend, and launches the browser desk at `http://localhost:3000`:

```powershell
.\start-web.ps1
```

---

### Option B: Manual Service Startup

#### 1. Start Backend API Server
```powershell
.\.venv\Scripts\activate
python dashboard.py
# API runs at http://127.0.0.1:8000
```

#### 2. Start Next.js Frontend Desk
```powershell
cd web
npm run dev
# Web desk runs at http://localhost:3000
```

#### 3. Run CLI Backtest (Standalone)
Runs the SMA crossover on historical data (defaults to SPY from 2009 to present):
```powershell
python backtest.py
```
Output files generated in `output/`:
- `output/trades.csv` — Full trade list with P&L and round-trip costs
- `output/equity_curve.csv` — Bar-by-bar portfolio equity curve
- `output/data_SPY.csv` — Downloaded OHLCV market bars

#### 4. Run Paper Trading Bot
Runs continuous live execution on the latest completed daily bars:
```powershell
python livebot.py
```
- Fills logged to `logs/bot.log`
- Closed round-trips saved to `output/live_trades.csv`
- Push alerts sent to Telegram (if configured)
- The authorized two-way Telegram listener starts with the live bot

Telegram commands:
- `/status` — bot health, market state, account balances, and positions
- `/pnl` — Alpaca account day P&L and open-position unrealized P&L
- `/pause` — block new entries while keeping position exits enabled
- `/resume` — re-enable new entries
- `/backtest <SYMBOL> <STRATEGY>` — run one non-blocking backtest
- `/help` — command and strategy reference

Only `TELEGRAM_CHAT_ID` is authorized. Pause state is in memory and resets
to active whenever `livebot.py` restarts.

---

## Running Automated Tests

Run the full 43-test suite covering simulation math, strategy signals, API endpoints, Telegram commands, notifier, and Alpaca client:

```powershell
.\.venv\Scripts\activate
pytest -v
```

To run frontend linting and production build verification:

```powershell
cd web
npm run lint
npm run build
```

---

## Strategies & Capabilities

| Strategy ID | Name | Timeframes | Shorts | Description |
|---|---|---|---|---|
| `sma_crossover` | SMA Crossover | `1m`, `5m`, `15m`, `30m`, `1h`, `1d` | Optional (Reversal) | Buy on golden cross, sell / short on death cross. |
| `vwap_reversion` | VWAP Reversion | `1m`, `5m`, `15m`, `30m`, `1h` | Supported | Fades intraday deviation from session VWAP; zero-volume bar resilient. |
| `opening_range_breakout` | Opening Range Breakout | `1m`, `5m`, `15m`, `30m` | Supported | Trades breakouts of the first N minutes of the session with TP/SL multipliers. |
| `rsi_mean_reversion` | RSI Mean Reversion | `1m`, `5m`, `15m`, `30m`, `1h`, `1d` | Supported | Buys oversold RSI, shorts overbought RSI, exits at target midline. |

---

## Project Structure

```
tradebot/
├── config.py             # System knobs (SYMBOL, SMA periods, CAPITAL, etc.)
├── alpaca_service.py     # Centralized Alpaca client, order polling & bar handling
├── strategy.py           # Core SMA signal functions
├── strategies.py         # Strategy registry (SMA, VWAP, ORB, RSI)
├── engine.py             # Backtest simulation engine with linear costs & EOD exit
├── backtest.py           # CLI backtester runner
├── livebot.py            # Alpaca paper trading daemon with Telegram alerts
├── notifier.py           # Fail-safe Telegram message client
├── telegram_bot.py       # Authorized interactive command listener
├── dashboard.py          # Flask REST API server (:8000)
├── start-web.ps1         # One-click platform launcher script
├── requirements.txt      # Python dependencies (pytest, pandas, flask, etc.)
├── HANDSOFF.md           # Operational manual & architecture specifications
├── REVIEW_AND_TODO.md    # Quality review & future feature roadmap
├── tests/                # Automated pytest test suite
│   ├── test_engine.py
│   ├── test_strategies.py
│   ├── test_dashboard_api.py
│   ├── test_notifier.py
│   └── test_alpaca_service.py
├── output/               # Generated backtest CSVs and live trading logs
├── logs/                 # Rotating logs for livebot (bot.log) and API (dashboard.log)
└── web/                  # Next.js 16 Web Desk (TypeScript, Tailwind v4, shadcn/ui)
```

---

## Troubleshooting

- **API Unreachable in UI**: Ensure `dashboard.py` is running on port 8000 before opening the web desk.
- **Port 3000 / 8000 in use**: Terminate orphaned processes or run `Get-Process python, node | Stop-Process`.
- **Missing Market Data**: yfinance caps intraday historical bars (e.g. 7 days for 1m, 60 days for 5m-30m, 730 days for 1h). Use standard daily (`1d`) for long-term multi-year backtests.
- **Paper Trading Keys**: Ensure your Alpaca keys in `.env` are generated from the **Paper Trading** dashboard, never real money.
