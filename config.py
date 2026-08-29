from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"

# ---- Strategy ----
SYMBOL = "SPY"
WATCHLIST = [s.strip().upper() for s in os.getenv("WATCHLIST", "SPY,QQQ,AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA").split(",") if s.strip()]
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

# ---- Local LLM advisor (LM Studio) ----
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
# Code default when LLM_MODEL is unset/blank in .env. Override via .env; use "auto" to auto-pick.
LLM_MODEL_DEFAULT = "prism-ml/bonsai-27b"


def resolve_llm_model_setting() -> tuple[str, bool]:
    """Return (model_name, auto_pick). Reads LLM_MODEL from the environment each call."""
    raw = os.getenv("LLM_MODEL")
    if raw is None:
        return LLM_MODEL_DEFAULT, False
    stripped = raw.strip()
    if not stripped:
        return LLM_MODEL_DEFAULT, False
    if stripped.lower() == "auto":
        return "", True
    return stripped, False

# ---- Telegram notifications (optional) ----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
TELEGRAM_HEARTBEAT_HOURS = 6  # periodic "bot alive" message interval

LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)