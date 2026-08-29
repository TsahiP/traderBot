"""Fail-safe Telegram messages for alerts and interactive replies."""
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

import config

logger = logging.getLogger("tradebot")


def send_telegram(text: str) -> bool:
    """Post a message to Telegram. Never raises; returns True on success."""
    return send_telegram_to(config.TELEGRAM_CHAT_ID, text)


def send_telegram_to(chat_id: str | int, text: str) -> bool:
    """Post a message to an explicit authorized chat."""
    token = config.TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        logger.info("Telegram not configured - skipping notification")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={"chat_id": str(chat_id), "text": text[:4096]},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False
