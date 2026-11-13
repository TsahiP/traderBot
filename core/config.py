from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")  # every entrypoint inherits the same env
except ImportError:
    pass

# ---- Strategy ----
SYMBOL = "SPY"
SMA_FAST = 10
SMA_SLOW = 50
QUANTITY = 10            # shares per trade (live bot: the cap when RISK_SIZE_BY_RISK is on)
CAPITAL = 100_000.0      # starting cash used by the backtest

# ---- Backtest ----
BACKTEST_START = "2009-01-01"

# ---- Live bot ----
POLL_INTERVAL_MIN = 5    # minutes between checks
ALPACA_PAPER = True      # always paper; never touch real money
ALPACA_BARS_LIMIT = 300  # enough history to warm up the slow SMA
LIVEBOT_STATE_PATH = OUTPUT_DIR / "livebot_state.json"  # open trade + today's entries (survives restarts)

# ---- Live bot exits (bracket order legs held at Alpaca, GTC) ----
ATR_PERIOD = 14          # daily ATR used to place the stop
STOP_ATR_MULT = 2.0      # stop-loss = entry - this x ATR
TARGET_R = 2.0           # take-profit = entry + this x (entry - stop)

# ---- Live bot risk gate (risk.py; None switches a limit off, exits are never blocked) ----
RISK_MAX_LOSS_PER_TRADE_USD = 200.0   # qty x (entry - stop)
RISK_MAX_DAILY_LOSS_USD = 500.0       # sum of the max losses of today's entries
RISK_MAX_TRADES_PER_DAY = 3
RISK_MIN_REWARD_RISK = 1.5
RISK_MAX_POSITION_PCT = 25.0          # of account equity
RISK_CASH_BUFFER_PCT = 10.0           # cash that must stay free after a buy
RISK_NO_ENTRIES_BEFORE = "09:45"      # NY time; skips the opening auction swings
RISK_NO_ENTRIES_AFTER = "15:30"
# Size each entry to fit the limits above (max loss, position cap, cash buffer),
# capped at QUANTITY. False = always QUANTITY shares, and the gate blocks if too big.
RISK_SIZE_BY_RISK = True
LIVEBOT_LOCK_PATH = OUTPUT_DIR / "livebot.lock"  # pid; livebot and paperbot never trade together

# ---- Paper desk (paperbot.py: runs + journal + live UI push; paper/ package) ----
PAPERBOT_CONFIG_PATH = OUTPUT_DIR / "paperbot_config.json"  # runs, risk, LLM / approval modes
PAPERBOT_DB_PATH = OUTPUT_DIR / "paperbot.db"               # SQLite journal (WAL)
PAPERBOT_LOCK_PATH = OUTPUT_DIR / "paperbot.lock"
PAPERBOT_PORT = int(os.getenv("PAPERBOT_PORT", "8100"))     # HTTP + SSE, bound to 127.0.0.1

# ---- Local LLM advisor (LM Studio) ----
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "")  # empty = auto-pick a chat model from LM Studio
LLM_LOAD_TIMEOUT = int(os.getenv("LLM_LOAD_TIMEOUT", "420"))  # seconds for native pre-load
LLM_IDLE_TTL_S = int(os.getenv("LLM_IDLE_TTL_S", "900"))  # unload after this many idle seconds; 0 = stay loaded

# ---- Discord analyst room (analystbot: select ticker -> LLM analysis) ----
ANALYST_CHANNEL_NAME = os.getenv("DISCORD_ANALYST_CHANNEL", "fundi-analyst")
ANALYST_EXTRA_SYMBOLS = [s.strip().upper() for s in os.getenv("ANALYST_EXTRA_SYMBOLS", "").split(",") if s.strip()]  # menu-only tickers (no watchlist side effects)
ANALYST_COOLDOWN_S =int(os.getenv("ANALYST_COOLDOWN_S", "20"))   # per user between clicks
ANALYST_MENU_REFRESH_MIN = float(os.getenv("ANALYST_MENU_REFRESH_MIN", "10"))
ANALYST_NEWS_MAX = int(os.getenv("ANALYST_NEWS_MAX", "6"))        # news items fed to the model
ANALYST_SIGNALS_MAX = int(os.getenv("ANALYST_SIGNALS_MAX", "8"))  # recent bot signals fed to the model
ANALYST_LLM_TIMEOUT = int(os.getenv("ANALYST_LLM_TIMEOUT", "600"))
ANALYST_RISK_USD = float(os.getenv("ANALYST_RISK_USD", "250"))   # step-5 default $ risk per trade
ANALYST_ACCOUNT_USD = float(os.getenv("ANALYST_ACCOUNT_USD", "0"))  # >0 caps step-5 size (25% position, cash)
ANALYST_REPLY_MODE = os.getenv("ANALYST_REPLY_MODE", "dm").strip().lower()  # "dm" = analysis to the requester's DMs (channel fallback), "channel" = public
ANALYST_TARGET_RR = float(os.getenv("ANALYST_TARGET_RR", "3"))  # reward:risk of the Discord "invest $" fixed-ratio plan
ANALYST_VISION = os.getenv("ANALYST_VISION", "1").strip() not in ("0", "false", "False", "off")
ANALYST_REASONING_EFFORT = os.getenv("ANALYST_REASONING_EFFORT", "none").strip()  # "" = do not send; "none" turns reasoning off (LM Studio)
MACRO_TTL_MIN = float(os.getenv("MACRO_TTL_MIN", "10"))         # step-2 macro context cache

# ---- Signal bot (candlestick patterns -> Telegram) ----
SIGNAL_CONFIG_PATH = OUTPUT_DIR / "signal_config.json"
SIGNAL_LOG_PATH = OUTPUT_DIR / "signal_log.json"
SIGNAL_HEARTBEAT_PATH = OUTPUT_DIR / "signal_heartbeat.json"
DISCORD_WEBHOOKS_PATH = OUTPUT_DIR / "discord_webhooks.json"
SIGNAL_MAX_LOG = 1000          # sent alerts kept in the log (also dedupe source)
# Time Machine line on intraday stock alerts (timemachine.py): replays of the rest of
# the session from 60 days of 5m bars. 0 turns it off.
TIMEMACHINE_ALERT_RUNS = int(os.getenv("TIMEMACHINE_ALERT_RUNS", "2000"))
TIMEMACHINE_ALERT_TFS = ("5m", "15m", "30m", "1h")

# ---- Fundamentals & news (fundabot / dashboard tab) ----
FUNDA_DIR = OUTPUT_DIR / "funda"           # per-symbol snapshot JSONs
NEWS_DIR = OUTPUT_DIR / "news"             # per-symbol recent news JSONs
FUNDA_STATE_PATH = OUTPUT_DIR / "funda_state.json"       # provider quota ledger + pass timestamps
FUNDA_HEARTBEAT_PATH = OUTPUT_DIR / "funda_heartbeat.json"
NEWS_SENT_PATH = OUTPUT_DIR / "news_sent.json"           # pushed-item hashes (Discord dedupe)
FUNDA_TTL_HOURS = 20           # a snapshot is fresh for ~a trading day
NEWS_MAX_PER_SYMBOL = 5        # new items pushed to Discord per poll per symbol
NEWS_MAX_PER_POLL = 25         # hard cap of pushes per poll
NEWS_LLM_SUMMARY = os.getenv("NEWS_LLM_SUMMARY", "1").strip() not in ("0", "false", "False", "off")  # fundabot: read each article + summarize via LM Studio
NEWS_LLM_MODEL = os.getenv("NEWS_LLM_MODEL", "")              # empty = same model as LLM_MODEL / auto-pick
NEWS_SUMMARY_LANG = os.getenv("NEWS_SUMMARY_LANG", "Hebrew")  # language of the Discord summary
NEWS_ARTICLE_MAX_CHARS = 6000  # article text fed to the model
NEWS_SUMMARY_TIMEOUT = 120     # seconds per LLM summary
NEWS_KEEP_PER_SYMBOL = 50      # items kept on disk per symbol
NEWS_MAX_LOG = 5000            # rolling dedupe window for pushed items
# Free-tier daily call caps (headroom below the provider's published limit)
FMP_DAILY_CAP = 220            # FMP Basic: 250/day
ALPHAVANTAGE_DAILY_CAP = 25    # Alpha Vantage free: 25/day + 5/min
TWELVEDATA_DAILY_CAP = 700     # Twelve Data free: 800 credits/day
FINNHUB_DAILY_SOFT_CAP = 4000  # 60/min free; keep a sanity ceiling on total calls

LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
FUNDA_DIR.mkdir(exist_ok=True)
NEWS_DIR.mkdir(exist_ok=True)