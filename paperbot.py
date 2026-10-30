"""Paper desk bot: trades the Alpaca PAPER account with the runs in
output/paperbot_config.json and journals everything to output/paperbot.db.

Run:
    python paperbot.py
Stop with Ctrl+C. Never run it together with livebot.py (same account) - the
lock files refuse that. Design: docs/superpowers/specs/2026-10-30-paper-desk-design.md
"""
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

import config
from paper import lock
from paper.bot import Bot
from paper.broker import Broker, BrokerError
from paper.bus import EventBus
from paper.journal import Journal
from paper.notify import DiscordNotifier
from paper.server import start_server
from paper.streams import QuoteStreamThread, TradingStreamThread

NY = ZoneInfo("America/New_York")
TICK_S = 1.0
logger = logging.getLogger("paperbot")


def setup_logging() -> None:
    """Console + logs/paperbot.log, attached only when run as the bot (tests never write the log)."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    logger.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    handler = RotatingFileHandler(config.LOG_DIR / "paperbot.log", maxBytes=1_000_000, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(fmt)
    logger.addHandler(console)
    logger.addHandler(handler)


def main() -> None:
    setup_logging()
    ok, reason = lock.acquire(config.PAPERBOT_LOCK_PATH, other=config.LIVEBOT_LOCK_PATH)
    if not ok:
        sys.exit(f"paperbot not started: {reason}")
    journal = bus = notifier = None
    streams = []
    try:
        load_dotenv(config.BASE_DIR / ".env")
        try:
            broker = Broker.from_env()
        except BrokerError as exc:
            sys.exit(str(exc))
        key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
        journal = Journal(config.PAPERBOT_DB_PATH)
        bus = EventBus(journal)
        bot = Bot(broker, journal, bus, config.PAPERBOT_CONFIG_PATH)
        notifier = DiscordNotifier(lambda: bot.cfg)
        bus.add_listener(notifier)
        notifier.start()
        trading = TradingStreamThread(bot.inbox, key, secret)
        quotes = QuoteStreamThread(bot.inbox, key, secret)
        bot.streams["trading"] = trading
        bot.quotes = quotes
        streams = [trading, quotes]

        logger.info("paperbot starting (pid %s, config %s)", os.getpid(), bot.cfg_hash)
        start_server(bot, config.PAPERBOT_PORT)
        trading.start()
        bot.start(datetime.now(NY))
        while True:
            bot.wait(TICK_S)
            try:
                bot.tick(datetime.now(NY))
            except Exception as exc:
                logger.exception("tick failed: %s", exc)
                bus.publish("error", {"message": f"tick failed: {exc}"})
    except KeyboardInterrupt:
        logger.info("paperbot stopping (Ctrl+C)")
    finally:
        for s in streams:
            s.stop()
        if bus is not None:
            bus.publish("bot.stop", {"pid": os.getpid()})
        if notifier is not None:
            notifier.stop(timeout=10.0)     # lets the "stopped" message go out
        if journal is not None:
            journal.close()
        lock.release(config.PAPERBOT_LOCK_PATH)


if __name__ == "__main__":
    main()
