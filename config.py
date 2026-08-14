from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"

# ---- Strategy ----
SYMBOL = "SPY"
SMA_FAST = 10
SMA_SLOW = 50
QUANTITY = 10            # shares bought/sold per trade
CAPITAL = 100_000.0      # starting cash used by the backtest

# ---- Backtest ----
BACKTEST_START = "2009-01-01"

# ---- Live bot ----
POLL_INTERVAL_MIN = 5    # minutes between checks
ALPACA_PAPER = True      # always paper; never touch real money
ALPACA_BARS_LIMIT = 300  # enough history to warm up the slow SMA

LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)