# TradeBot — Comprehensive Review, QA & Roadmap

Review date: 2026-08-28.

## 1. Quality Assurance & Bug Fixes Completed

| Bug / Vulnerability | Root Cause | Fix Applied | Status |
|---|---|---|---|
| **Quadratic Cost Inflation** (`engine.py`) | Multiplied `cost = cost_per_share * qty` by `qty` again on entry, exit, and PnL | Corrected to linear `cost_per_trade = cost_per_share * qty` | Fixed & Tested |
| **Rogue Long Buys on Short Cover** (`strategies.py`) | Stateful strategies emitted `signal = 1` to cover shorts even when `allow_short=False` | Passed `allow_short` into strategies; short entries are skipped when disabled | Fixed & Tested |
| **SMA Short Reversal Dropped** (`engine.py`) | Death cross only exited long to flat when `allow_short=True` without opening short | Implemented position flipping on reversal signals | Fixed & Tested |
| **VWAP NaN Propagation** (`strategies.py`) | Zero volume replaced `tp` with `NaN`, corrupting `cumsum()` for rest of day | Replaced denominator zero only and added forward/back fills | Fixed & Tested |
| **RSI Trend Ceiling/Floor** (`strategies.py`) | Pure upward trend had 0 downward roll, producing NaN RSI instead of 100 | Handled zero down-moves with Wilder's standard RSI boundaries | Fixed & Tested |
| **Negative Equity Crash** (`engine.py`) | Bankruptcy (`equity <= 0`) raised ValueError / complex numbers in CAGR root | Added guard returning `-1.0` (-100%) on bankruptcy | Fixed & Tested |
| **Livebot KeyError on Restart** (`livebot.py`) | `open_trade = {}` accessed `open_trade["entry_price"]` on sell if started while in position | Reconnected to Alpaca position details on startup | Fixed & Tested |
| **Instant Fallback on Orders** (`livebot.py`) | Did not wait for Alpaca paper order fill status | Added retry loop with brief backoff via `alpaca_service.py` | Fixed & Tested |
| **Stats Missing Costs** (`dashboard.py`) | `load_backtest_trades()` dropped `costs` column | Carried `costs` column so `/api/stats` reports true total costs | Fixed & Tested |
| **Sort Crash on Open Trades** (`dashboard.py`) | `None` exit date in open trades crashed `trades.sort()` | Added `t.get("exit_date") or t.get("entry_date") or ""` safe key | Fixed & Tested |
| **Duplicated Alpaca Boilerplate** | Handled separately across `livebot.py` and `dashboard.py` | Centralized in `alpaca_service.py` | Refactored |
| **Missing API Server Logs** | `dashboard.py` had no persistent file logging | Added rotating `logs/dashboard.log` (1 MB x 3) | Added |
| **Missing Automated Tests** | No automated tests or pytest in repository | Created `tests/` with 20 unit/integration tests | 20/20 Passing |

---

## 2. Best Practices Implemented

1. **Shared Alpaca Service (`alpaca_service.py`)**:
   - Single point of initialization, credentials validation, and error trapping for live bot and dashboard API.
2. **Strict Timezone Handling**:
   - Normalized all bar timestamps to New York timezone (`America/New_York`) to ensure clean session date comparisons.
3. **Linear Accounting & Execution Model**:
   - Pure separation of order generation, execution at next bar open, linear commission/slippage subtraction, and session-end EOD close.
4. **Comprehensive Test Coverage (`tests/`)**:
   - Covers pure math, strategies, API response schemas, and external notification mocking.

---

## 3. Future Feature Roadmap & Backlog

### Phase 1: Risk Management & Advanced Position Sizing
- [ ] **ATR-Based Dynamic Volatility Sizing**: Calculate position size as `(Capital * Risk_Pct) / (ATR * Multiplier)` instead of fixed share quantity.
- [ ] **Portfolio Daily Loss Limit (Kill-Switch)**: If realized + unrealized daily loss exceeds `MAX_DAILY_LOSS_PCT` (e.g., 3%), liquidate open positions and halt live trading for the remainder of the day.
- [ ] **Trailing Stop-Loss & Take-Profit Engine**: Support dynamic trailing stops (`trailing_pct`) in `engine.py` and `livebot.py`.

### Phase 2: Multi-Asset Scanner & Screener API
- [x] **Multi-Ticker Watchlist Loop**: Loop through configured liquid symbols (`config.WATCHLIST`) in `livebot.py` with multi-position synchronization and per-symbol trade tracking in `live_trades.csv`.
- [x] **Stock Analysis 8-Dimension Engine Integration (`/api/stock-analysis`)**:
  - Implemented in `stock_analysis.py` with dynamic weight normalization across 8 quantitative/fundamental dimensions (Earnings Surprise, Valuation Fundamentals, Analyst Sentiment, Historical Volatility, Market Context, Sector Alpha, Momentum/RSI, and Sentiment/Risk Flags).
- [x] **Crypto Top-20 Market Monitor (`/api/crypto-analysis`)**:
  - Implemented in `crypto_analysis.py` with rolling 30-day BTC Pearson correlation, 30-day annualized realized volatility regimes, RSI momentum scoring, and category filtering.
- [x] **Interactive Market Scanner UI (`web/`)**:
  - Added Market Scanner tab in Next.js frontend with 8-Dimension Stock Scorecard and Top-20 Crypto Intelligence Matrix table.
- [x] **Automated Test Suite (34/34 Passing)**:
  - Added unit and integration tests across multi-ticker livebot loop, stock analysis dimensions, crypto volatility regimes, and API endpoints.

### Phase 3: Interactive Telegram 2-Way Bot
- [x] **Interactive Commands**:
  - `/status`: Returns bot health, account equity, buying power, and active positions.
  - `/pnl`: Summary of today's realized and unrealized P&L.
  - `/pause` and `/resume`: Remote execution gating.
  - `/backtest <SYMBOL> <STRATEGY>`: Trigger on-demand backtests directly from chat.
  - Implemented as an authorized long-poll listener embedded in `livebot.py`; pause blocks new entries while preserving exits, and backtests run asynchronously one at a time.
- [x] **Automated Test Suite (43/43 Passing)**:
  - Added command authorization, polling offsets, status/P&L formatting, pause gating, asynchronous backtest locking, and account day-P&L coverage.

### Phase 4: Strategy Lab Optimization & Frontend Enhancements
- [ ] **Parameter Heatmap / Grid-Search Optimizer**: Run batch parameter permutations across historical data to produce Sharpe ratio and profit factor heatmaps.
- [ ] **Side-by-Side Strategy Comparison**: Compare equity curves of 2-3 strategies on the same chart.
- [ ] **Export to PDF / CSV**: Export backtest tear-sheets with monthly returns table and drawdown distribution.
